import numpy as np
import json
import subprocess
import sys
import tempfile
import os
import plotly.graph_objects as go
import plotly
from plotly.subplots import make_subplots

# ── Display constants (ms) ────────────────────────────────────────────────────
_RF_DUR   = 4.0   # RF pulse duration
_PREP_DUR = 2.0   # prephase block duration
_RO_DUR   = 4.0   # readout duration
_RISE     = 0.4   # gradient rise time

_FILL = {
    'royalblue': 'rgba(65,105,225,0.20)',
    'navy':      'rgba(0,0,128,0.20)',
    'green':     'rgba(0,128,0,0.20)',
    'darkgreen': 'rgba(0,100,0,0.20)',
    'orange':    'rgba(255,165,0,0.20)',
    'purple':    'rgba(128,0,128,0.20)',
    'red':       'rgba(220,0,0,0.25)',
    'gray':      'rgba(80,80,80,0.15)',
}

_CODE_PREAMBLE = '''\
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as _plt
import io as _io, base64 as _b64

def _show_hook():
    _buf = _io.BytesIO()
    _plt.savefig(_buf, format='png', dpi=100, bbox_inches='tight')
    _buf.seek(0)
    _b64str = _b64.b64encode(_buf.read()).decode()
    _buf.close()
    _plt.close('all')
    print(f"<<<FIGURE_B64>>>{_b64str}<<<END_FIGURE>>>", flush=True)

_plt.show = _show_hook
# ─── user code ───────────────────────────────────────────────────────────────
'''


# ── Waveform helpers ──────────────────────────────────────────────────────────

def _trap(t0, dur, amp):
    r = min(_RISE, dur / 2.0)
    t = [t0, t0 + r, t0 + dur - r, t0 + dur]
    a = [0.0, amp, amp, 0.0]
    return t, a


def _sinc(t0, dur, amp, n=40):
    t = np.linspace(t0, t0 + dur, n)
    x = (t - (t0 + dur / 2)) / (dur / 2) * 3.5
    a = amp * np.sinc(x / np.pi) * np.exp(-0.35 * x ** 2)
    return t.tolist(), a.tolist()


def _rect(t0, t1, amp):
    eps = 0.02
    return [t0 - eps, t0, t0, t1, t1, t1 + eps], [0.0, 0.0, amp, amp, 0.0, 0.0]


# ── Sequence event builders ───────────────────────────────────────────────────

def _gre_events(te, tr, fa):
    """Return list of (channel, t_pts, a_pts, label, color) for GRE."""
    rf_center = _RF_DUR / 2
    prep_end = _RF_DUR + _PREP_DUR
    te_min = _RF_DUR / 2 + _PREP_DUR + _RO_DUR / 2
    te = max(te, te_min)
    echo_t = rf_center + te
    ro_s = echo_t - _RO_DUR / 2
    ro_e = echo_t + _RO_DUR / 2
    tr = max(tr, ro_e + 1.0)

    ev = []
    ev.append(('rf', *_sinc(0, _RF_DUR, fa / 90), f'RF ({fa:.0f}°)', 'royalblue'))
    ev.append(('gz', *_trap(0, _RF_DUR, 1.0), 'Gss', 'green'))
    ev.append(('gz', *_trap(_RF_DUR, _PREP_DUR, -0.5), 'Gss rephase', 'green'))
    ev.append(('gx', *_trap(_RF_DUR, _PREP_DUR, -1.0), 'Gx prephase', 'orange'))
    ev.append(('gy', *_trap(_RF_DUR, _PREP_DUR, 0.6), 'Gy (PE)', 'purple'))
    ev.append(('gx', *_trap(ro_s, _RO_DUR, 1.0), 'Gx readout', 'orange'))
    ev.append(('adc', *_rect(ro_s, ro_e, 0.85), 'ADC', 'red'))
    return ev, tr, ro_e


def _se_events(te, tr, fa):
    rf_center = _RF_DUR / 2
    prep_end = _RF_DUR + _PREP_DUR
    te_min = 2 * max(_RF_DUR / 2 + _PREP_DUR + _RF_DUR / 2,
                     _RF_DUR / 2 + _RO_DUR / 2)
    te = max(te, te_min)
    inv_center = rf_center + te / 2
    inv_s = inv_center - _RF_DUR / 2
    inv_e = inv_center + _RF_DUR / 2
    echo_t = rf_center + te
    ro_s = echo_t - _RO_DUR / 2
    ro_e = echo_t + _RO_DUR / 2
    tr = max(tr, ro_e + 1.0)

    ev = []
    ev.append(('rf', *_sinc(0, _RF_DUR, fa / 90), f'RF {fa:.0f}° (exc)', 'royalblue'))
    ev.append(('rf', *_sinc(inv_s, _RF_DUR, 1.0), 'RF 180° (ref)', 'navy'))
    ev.append(('gz', *_trap(0, _RF_DUR, 1.0), 'Gss exc', 'green'))
    ev.append(('gz', *_trap(_RF_DUR, _PREP_DUR, -0.5), 'Gss rephase', 'green'))
    ev.append(('gz', *_trap(inv_s, _RF_DUR, 1.0), 'Gss ref', 'darkgreen'))
    ev.append(('gx', *_trap(_RF_DUR, _PREP_DUR, -1.0), 'Gx prephase', 'orange'))
    ev.append(('gy', *_trap(_RF_DUR, _PREP_DUR, 0.6), 'Gy (PE)', 'purple'))
    ev.append(('gx', *_trap(ro_s, _RO_DUR, 1.0), 'Gx readout', 'orange'))
    ev.append(('adc', *_rect(ro_s, ro_e, 0.85), 'ADC', 'red'))
    return ev, tr, ro_e


def _ir_events(te, tr, ti, fa):
    # Inversion pulse at t=0
    exc_s = ti  # excitation RF starts at TI (from center of inv to center of exc)
    exc_center = exc_s + _RF_DUR / 2
    exc_e = exc_s + _RF_DUR
    prep_e = exc_e + _PREP_DUR
    echo_t = exc_center + te
    ro_s = echo_t - _RO_DUR / 2
    ro_e = echo_t + _RO_DUR / 2
    tr = max(tr, ro_e + 1.0)

    ev = []
    ev.append(('rf', *_sinc(0, _RF_DUR, 1.0), 'RF 180° (inv)', 'navy'))
    ev.append(('gz', *_trap(0, _RF_DUR, 1.0), 'Gss inv', 'green'))
    ev.append(('rf', *_sinc(exc_s, _RF_DUR, fa / 90), f'RF {fa:.0f}° (exc)', 'royalblue'))
    ev.append(('gz', *_trap(exc_s, _RF_DUR, 0.8), 'Gss exc', 'darkgreen'))
    ev.append(('gz', *_trap(exc_e, _PREP_DUR, -0.4), 'Gss rephase', 'green'))
    ev.append(('gx', *_trap(exc_e, _PREP_DUR, -1.0), 'Gx prephase', 'orange'))
    ev.append(('gy', *_trap(exc_e, _PREP_DUR, 0.6), 'Gy (PE)', 'purple'))
    ev.append(('gx', *_trap(ro_s, _RO_DUR, 1.0), 'Gx readout', 'orange'))
    ev.append(('adc', *_rect(ro_s, ro_e, 0.85), 'ADC', 'red'))
    return ev, tr, ro_e


# ── Diagram builder ───────────────────────────────────────────────────────────

def make_diagram(seq_type, params):
    te = float(params.get('te', 6.0))
    tr = float(params.get('tr', 25.0))
    fa = float(params.get('fa', 30.0))
    ti = float(params.get('ti', 100.0))

    if seq_type == 'gre':
        events, tr_end, ro_e = _gre_events(te, tr, fa)
    elif seq_type == 'se':
        events, tr_end, ro_e = _se_events(te, tr, fa)
    else:
        events, tr_end, ro_e = _ir_events(te, tr, ti, fa)

    ch_row = {'rf': 1, 'gz': 2, 'gy': 3, 'gx': 4, 'adc': 5}
    ch_ylbl = {1: 'RF', 2: 'Gz', 3: 'Gy', 4: 'Gx', 5: 'ADC'}

    # Clip x-axis: show up to ro_e + small margin or 250ms max
    x_max = min(ro_e + max(5.0, tr_end * 0.05), 250.0)

    fig = make_subplots(
        rows=5, cols=1,
        shared_xaxes=True,
        row_heights=[1.8, 1.0, 1.0, 1.0, 0.7],
        vertical_spacing=0.04,
    )

    seen_labels = set()
    for ch, t_pts, a_pts, label, color in events:
        row = ch_row.get(ch, 1)
        show_leg = label not in seen_labels
        seen_labels.add(label)
        fig.add_trace(
            go.Scatter(
                x=t_pts, y=a_pts,
                mode='lines',
                name=label,
                line=dict(color=color, width=2),
                fill='tozeroy',
                fillcolor=_FILL.get(color, 'rgba(100,100,100,0.15)'),
                showlegend=show_leg,
                legendgroup=label,
            ),
            row=row, col=1,
        )

    for r in range(1, 6):
        fig.update_yaxes(
            title_text=ch_ylbl[r],
            title_font=dict(size=10),
            range=[-1.4, 1.6] if r < 5 else [0, 1.2],
            showticklabels=False,
            zeroline=True,
            zerolinecolor='#ccc',
            zerolinewidth=1,
            row=r, col=1,
        )
        fig.update_xaxes(range=[0, x_max], row=r, col=1)

    fig.update_xaxes(title_text='Time (ms)', row=5, col=1)

    title_map = {'gre': 'GRE', 'se': 'Spin Echo', 'ir': 'Inversion Recovery'}
    fig.update_layout(
        title=dict(
            text=f'{title_map.get(seq_type,"Sequence")} — TR={tr:.0f} ms, TE={te:.0f} ms'
                 + (f', TI={ti:.0f} ms' if seq_type == 'ir' else ''),
            x=0.5, font_size=12,
        ),
        height=500,
        margin=dict(l=50, r=10, t=45, b=40),
        plot_bgcolor='white',
        paper_bgcolor='white',
        legend=dict(
            orientation='h', yanchor='bottom', y=1.04,
            xanchor='right', x=1, font_size=10,
        ),
    )
    return json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)


# ── k-space trajectory ────────────────────────────────────────────────────────

def make_kspace(seq_type, params):
    Ny = 8
    kys = np.arange(Ny) - Ny // 2  # -4 … 3

    colors = [
        f'rgba({int(20 + 220 * i / (Ny - 1))},100,{int(200 - 180 * i / (Ny - 1))},0.9)'
        for i in range(Ny)
    ]

    fig = go.Figure()
    for i, ky in enumerate(kys):
        ky_f = float(ky)
        # Readout line
        fig.add_trace(go.Scatter(
            x=[-1, 1], y=[ky_f, ky_f],
            mode='lines',
            line=dict(color=colors[i], width=2.5 if ky == 0 else 1.5),
            showlegend=False,
        ))
        # Arrow tip (triangle marker at kx=+1 to show direction)
        fig.add_trace(go.Scatter(
            x=[1], y=[ky_f],
            mode='markers',
            marker=dict(symbol='triangle-right', size=8, color=colors[i]),
            showlegend=False,
        ))

    # Mark center
    fig.add_trace(go.Scatter(
        x=[-1, 1], y=[0, 0],
        mode='lines',
        line=dict(color='crimson', width=3, dash='dot'),
        name='ky=0 (center)',
    ))

    fig.update_layout(
        title='k-space trajectory (8 PE lines shown)',
        xaxis_title='kx (read direction)',
        yaxis_title='ky (phase encode)',
        height=280,
        margin=dict(l=50, r=10, t=40, b=40),
        plot_bgcolor='white',
        xaxis=dict(zeroline=True, zerolinecolor='#bbb', range=[-1.3, 1.3]),
        yaxis=dict(zeroline=True, zerolinecolor='#bbb'),
        legend=dict(x=0, y=1.0, font_size=10),
    )
    return json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)


# ── Code generator ────────────────────────────────────────────────────────────

def make_code(seq_type, params):
    te_ms = float(params.get('te', 6.0))
    tr_ms = float(params.get('tr', 25.0))
    fa    = float(params.get('fa', 30.0))
    ti_ms = float(params.get('ti', 100.0))
    fov   = float(params.get('fov', 250.0))
    thk   = float(params.get('thk', 5.0))
    Nx    = int(params.get('Nx', 64))
    Ny    = int(params.get('Ny', 64))

    hdr = f'''\
import pypulseq as pp
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

system = pp.Opts(
    max_grad=28, grad_unit='mT/m',
    max_slew=150, slew_unit='T/m/s',
    rf_ringdown_time=20e-6,
    rf_dead_time=100e-6,
    adc_dead_time=20e-6,
)
seq = pp.Sequence(system=system)

flip       = {fa}         # flip angle [deg]
TR         = {tr_ms}e-3   # repetition time [s]
TE         = {te_ms}e-3   # echo time [s]
fov        = {fov}e-3     # field of view [m]
Nx, Ny     = {Nx}, {Ny}   # matrix size
slice_thk  = {thk}e-3    # slice thickness [m]
'''

    if seq_type == 'gre':
        return hdr + f'''\

# ── GRE sequence ──────────────────────────────────────────────────────────────

rf, gz, gz_reph = pp.make_sinc_pulse(
    flip_angle=np.deg2rad(flip),
    duration=4e-3,
    slice_thickness=slice_thk,
    apodization=0.5,
    time_bw_product=4,
    system=system,
    return_gz=True,
)

delta_k = 1 / fov
gx = pp.make_trapezoid(channel='x', flat_area=Nx * delta_k,
                        flat_time=4e-3, system=system)
adc = pp.make_adc(num_samples=Nx, duration=gx.flat_time,
                   delay=gx.rise_time, system=system)
gx_pre = pp.make_trapezoid(channel='x', area=-gx.area / 2,
                             duration=2e-3, system=system)

gy_areas = (np.arange(Ny) - Ny // 2) * (1 / fov)
gy_max   = pp.make_trapezoid(channel='y',
                              area=float(np.max(np.abs(gy_areas))),
                              duration=2e-3, system=system)

# Timing
te_delay = (TE - pp.calc_duration(gz) / 2
            - pp.calc_duration(gx_pre)
            - pp.calc_duration(gx) / 2)
tr_fill  = (TR - TE
            - pp.calc_duration(gz) / 2
            - pp.calc_duration(gx) / 2)
assert te_delay >= 0, f"TE too short (min ≈ {{(pp.calc_duration(gz)/2+pp.calc_duration(gx_pre)+pp.calc_duration(gx)/2)*1e3:.1f}} ms)"
assert tr_fill  >= 0, "TR too short"

for iy in range(Ny):
    scale = gy_areas[iy] / float(np.max(np.abs(gy_areas)))
    gy = pp.scale_grad(gy_max, scale)
    seq.add_block(rf, gz)
    if te_delay > 1e-7:
        seq.add_block(gx_pre, gz_reph, gy, pp.make_delay(te_delay))
    else:
        seq.add_block(gx_pre, gz_reph, gy)
    seq.add_block(gx, adc)
    seq.add_block(pp.make_delay(tr_fill))

seq.plot(time_range=(0, TR))
plt.suptitle(f'GRE  FA={{flip}}°  TR={{TR*1e3:.0f}} ms  TE={{TE*1e3:.0f}} ms')
plt.tight_layout()
plt.show()

ok, err = seq.check_timing()
print(f"Timing: {{'OK' if ok else err}}")
print(f"Total duration: {{seq.duration()[0]*1e3:.0f}} ms")
'''

    elif seq_type == 'se':
        return hdr + f'''\

# ── Spin Echo sequence ────────────────────────────────────────────────────────

rf90, gz90, gz90r = pp.make_sinc_pulse(
    flip_angle=np.deg2rad(90),
    duration=4e-3,
    slice_thickness=slice_thk,
    apodization=0.5,
    time_bw_product=4,
    system=system,
    return_gz=True,
)

# 180° refocusing (non-selective for simplicity)
rf180 = pp.make_sinc_pulse(
    flip_angle=np.deg2rad(180),
    duration=4e-3,
    system=system,
    use='refocusing',
)

delta_k = 1 / fov
gx = pp.make_trapezoid(channel='x', flat_area=Nx * delta_k,
                        flat_time=4e-3, system=system)
adc = pp.make_adc(num_samples=Nx, duration=gx.flat_time,
                   delay=gx.rise_time, system=system)
gx_pre = pp.make_trapezoid(channel='x', area=-gx.area / 2,
                             duration=2e-3, system=system)
gy_areas = (np.arange(Ny) - Ny // 2) * (1 / fov)
gy_max   = pp.make_trapezoid(channel='y',
                              area=float(np.max(np.abs(gy_areas))),
                              duration=2e-3, system=system)

# Timing (TE/2 symmetric about 180° pulse)
d90  = pp.calc_duration(gz90)
d180 = pp.calc_duration(rf180)
d_ro = pp.calc_duration(gx)
d_pre = pp.calc_duration(gx_pre)

half1 = TE / 2 - d90 / 2 - d_pre - d180 / 2
half2 = TE / 2 - d180 / 2 - d_ro / 2
tr_fill = TR - TE - d90 / 2 - d_ro / 2
assert half1 >= 0, f"TE too short (1st half, min TE ≈ {{2*(d90/2+d_pre+d180/2)*1e3:.1f}} ms)"
assert half2 >= 0, f"TE too short (2nd half, min TE ≈ {{2*(d180/2+d_ro/2)*1e3:.1f}} ms)"
assert tr_fill >= 0, "TR too short"

for iy in range(Ny):
    scale = gy_areas[iy] / float(np.max(np.abs(gy_areas)))
    gy = pp.scale_grad(gy_max, scale)
    seq.add_block(rf90, gz90)
    if half1 > 1e-7:
        seq.add_block(gx_pre, gz90r, gy, pp.make_delay(half1))
    else:
        seq.add_block(gx_pre, gz90r, gy)
    seq.add_block(rf180)
    if half2 > 1e-7:
        seq.add_block(pp.make_delay(half2))
    seq.add_block(gx, adc)
    seq.add_block(pp.make_delay(tr_fill))

seq.plot(time_range=(0, TR))
plt.suptitle(f'SE  TR={{TR*1e3:.0f}} ms  TE={{TE*1e3:.0f}} ms')
plt.tight_layout()
plt.show()

ok, err = seq.check_timing()
print(f"Timing: {{'OK' if ok else err}}")
print(f"Total duration: {{seq.duration()[0]*1e3:.0f}} ms")
'''

    else:  # ir
        return hdr + f'''\
TI = {ti_ms}e-3  # inversion time [s]

# ── Inversion Recovery sequence ───────────────────────────────────────────────

# Non-selective 180° inversion pulse
rf_inv = pp.make_sinc_pulse(
    flip_angle=np.deg2rad(180),
    duration=4e-3,
    system=system,
)

rf90, gz90, gz90r = pp.make_sinc_pulse(
    flip_angle=np.deg2rad(flip),
    duration=4e-3,
    slice_thickness=slice_thk,
    apodization=0.5,
    time_bw_product=4,
    system=system,
    return_gz=True,
)

delta_k = 1 / fov
gx = pp.make_trapezoid(channel='x', flat_area=Nx * delta_k,
                        flat_time=4e-3, system=system)
adc = pp.make_adc(num_samples=Nx, duration=gx.flat_time,
                   delay=gx.rise_time, system=system)
gx_pre = pp.make_trapezoid(channel='x', area=-gx.area / 2,
                             duration=2e-3, system=system)
gy_areas = (np.arange(Ny) - Ny // 2) * (1 / fov)
gy_max   = pp.make_trapezoid(channel='y',
                              area=float(np.max(np.abs(gy_areas))),
                              duration=2e-3, system=system)

# Timing
d_inv = pp.calc_duration(rf_inv)
d90   = pp.calc_duration(gz90)
d_pre = pp.calc_duration(gx_pre)
d_ro  = pp.calc_duration(gx)

ti_delay = TI - d_inv / 2 - d90 / 2
te_delay = TE - d90 / 2 - d_pre - d_ro / 2
tr_fill  = TR - TI - TE - d_inv / 2 - d90 / 2 - d_ro / 2
assert ti_delay >= 0, "TI too short"
assert te_delay >= 0, "TE too short"
assert tr_fill  >= 0, "TR too short"

for iy in range(Ny):
    scale = gy_areas[iy] / float(np.max(np.abs(gy_areas)))
    gy = pp.scale_grad(gy_max, scale)
    seq.add_block(rf_inv)
    seq.add_block(pp.make_delay(ti_delay))
    seq.add_block(rf90, gz90)
    if te_delay > 1e-7:
        seq.add_block(gx_pre, gz90r, gy, pp.make_delay(te_delay))
    else:
        seq.add_block(gx_pre, gz90r, gy)
    seq.add_block(gx, adc)
    seq.add_block(pp.make_delay(tr_fill))

seq.plot(time_range=(0, TR))
plt.suptitle(f'IR  FA={{flip}}°  TR={{TR*1e3:.0f}} ms  TE={{TE*1e3:.0f}} ms  TI={{TI*1e3:.0f}} ms')
plt.tight_layout()
plt.show()

ok, err = seq.check_timing()
print(f"Timing: {{'OK' if ok else err}}")
print(f"Total duration: {{seq.duration()[0]*1e3:.0f}} ms")
'''


# ── Code runner ───────────────────────────────────────────────────────────────

def run_user_code(user_code, timeout=15):
    """Execute user code in a subprocess. Return (stdout_text, stderr, [b64_figs])."""
    full_code = _CODE_PREAMBLE + user_code

    fd, fname = tempfile.mkstemp(suffix='.py')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(full_code)
        result = subprocess.run(
            [sys.executable, fname],
            capture_output=True, text=True, timeout=timeout,
        )
        raw_out = result.stdout
        stderr  = result.stderr
    except subprocess.TimeoutExpired:
        raw_out = ''
        stderr  = f'Execution timed out after {timeout} s.'
    except Exception as exc:
        raw_out = ''
        stderr  = f'Error launching subprocess: {exc}'
    finally:
        try:
            os.unlink(fname)
        except OSError:
            pass

    figures = []
    parts = raw_out.split('<<<FIGURE_B64>>>')
    text_out = parts[0]
    for part in parts[1:]:
        if '<<<END_FIGURE>>>' in part:
            b64, rest = part.split('<<<END_FIGURE>>>', 1)
            figures.append(b64.strip())
            text_out += rest

    return text_out.strip(), stderr.strip(), figures


# ── Main entry ────────────────────────────────────────────────────────────────

def game10_worker(seq_type, params):
    """Return (diagram_json, kspace_json, code_str) for guided mode."""
    return (
        make_diagram(seq_type, params),
        make_kspace(seq_type, params),
        make_code(seq_type, params),
    )
