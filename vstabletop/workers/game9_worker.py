from functools import lru_cache
import numpy as np
import scipy.fft as fft
import json
import plotly.graph_objects as go
import plotly
from scipy.ndimage import map_coordinates, binary_dilation, zoom

from vstabletop.workers.game2_worker import _shepp_logan_cached
from vstabletop.workers.game3_worker import _BRAINWEB, signal_model

_N = 256  # Working resolution for all phantoms


# ── Phantom generation ────────────────────────────────────────────────────────

def _norm(img):
    mn, mx = float(img.min()), float(img.max())
    if mx <= mn:
        return np.zeros_like(img, dtype=float)
    return (img - mn) / (mx - mn)


@lru_cache(maxsize=2)
def _base_phantom(phantom_type):
    """Return normalized [0,1] MR image at _N × _N resolution."""
    if phantom_type == 'shepp-logan':
        img = _shepp_logan_cached(_N).copy().astype(float)
        return _norm(img)
    # brainweb — T1-weighted (TR=500ms, TE=10ms, FA=90°)
    type_slice = _BRAINWEB['typemap'][:, :, 87]
    params = _BRAINWEB['params']
    mr = np.zeros(type_slice.shape, dtype=float)
    for ti in range(10):
        mr[type_slice == ti] = signal_model(
            params[ti, 3], params[ti, 0] / 1e3, params[ti, 1] / 1e3,
            500e-3, 10e-3, 90)
    mr = np.flipud(np.transpose(mr))
    if mr.shape != (_N, _N):
        mr = zoom(mr, (_N / mr.shape[0], _N / mr.shape[1]), order=1)
    return _norm(mr)


@lru_cache(maxsize=2)
def _fat_phantom(phantom_type):
    """Return [0,1] fat-only image at _N × _N resolution."""
    if phantom_type == 'shepp-logan':
        base = _base_phantom(phantom_type)
        mask = base > 0.05
        fat_ring = binary_dilation(mask, iterations=6) & ~mask
        return fat_ring.astype(float) * 0.8
    # brainweb — fat is tissue type 4
    type_slice = _BRAINWEB['typemap'][:, :, 87]
    params = _BRAINWEB['params']
    fat = np.zeros(type_slice.shape, dtype=float)
    fat[type_slice == 4] = signal_model(
        params[4, 3], params[4, 0] / 1e3, params[4, 1] / 1e3,
        500e-3, 10e-3, 90)
    fat = np.flipud(np.transpose(fat))
    if fat.shape != (_N, _N):
        fat = zoom(fat, (_N / fat.shape[0], _N / fat.shape[1]), order=1)
    return np.clip(fat, 0, None)


# ── Artifact functions ────────────────────────────────────────────────────────

def apply_aliasing(image, fov_fraction):
    """Wrap-around aliasing: reduce FOV in the PE (vertical) direction.

    When the PE FOV is smaller than the object, signal from outside wraps
    in to overlap with signal from inside. The tiled display repeats the
    folded image to show the periodic wrap-around pattern.
    """
    N = image.shape[0]
    fov_px = max(8, int(N * fov_fraction))
    # Fold all out-of-FOV rows into the reduced-FOV window
    result = np.zeros((fov_px, image.shape[1]), dtype=float)
    for start in range(0, N, fov_px):
        end = min(start + fov_px, N)
        chunk = image[start:end, :]
        result[:chunk.shape[0], :] += chunk
    # Tile back to N rows so both panels are the same display height
    repeats = int(np.ceil(N / fov_px))
    tiled = np.tile(result, (repeats, 1))[:N, :]
    return np.clip(tiled, 0, None)


def apply_gibbs(image, kspace_fraction):
    """Gibbs ringing from k-space truncation.

    Keeps only the central kspace_fraction% of k-space, zeroing the rest.
    Sharp edges (brain boundary, ventricles) show oscillatory overshoots.
    """
    N, M = image.shape
    ks = fft.fftshift(fft.fft2(image))
    keep_N = max(4, int(N * kspace_fraction / 100))
    keep_M = max(4, int(M * kspace_fraction / 100))
    cN, cM = N // 2, M // 2
    mask = np.zeros((N, M), dtype=float)
    mask[cN - keep_N // 2:cN + keep_N // 2,
         cM - keep_M // 2:cM + keep_M // 2] = 1.0
    return np.abs(fft.ifft2(fft.ifftshift(ks * mask)))


def apply_motion(image, amplitude, frequency):
    """Periodic ghosting from sinusoidal motion in the PE direction.

    Each PE k-space row is phase-modulated according to the object's position
    at that point in the acquisition. The resulting image shows ghost copies
    displaced in the PE direction, spaced by FOV / motion_frequency.
    """
    N = image.shape[0]
    ks = fft.fftshift(fft.fft2(image))
    pe_idx = np.arange(N)
    phase = amplitude * np.pi * np.sin(2 * np.pi * frequency * pe_idx / N)
    ks_corrupted = ks * np.exp(1j * phase[:, np.newaxis])
    return np.abs(fft.ifft2(fft.ifftshift(ks_corrupted)))


def apply_chemical_shift(water, fat, shift_pixels):
    """Chemical shift: fat displaced along the frequency-encode (horizontal) axis.

    Fat resonates ~3.5 ppm lower than water. In the frequency-encode
    direction, this frequency offset is misinterpreted as a spatial shift,
    displacing fat relative to water structures.
    """
    shift = int(round(shift_pixels))
    fat_shifted = np.roll(fat, shift, axis=1)
    return water + fat_shifted


def apply_susceptibility(image, sus_x, sus_y, sus_strength):
    """Geometric distortion and signal void from a focal susceptibility source.

    A susceptibility source (e.g. air cavity, surgical clip) creates a
    dipole-like B0 perturbation. This causes:
      1. Geometric distortion in the frequency-encode direction.
      2. Signal void due to local field gradients dephasing spins (T2* effect).
    """
    N, M = image.shape
    x = np.linspace(-1.0, 1.0, M)
    y = np.linspace(-1.0, 1.0, N)
    X, Y = np.meshgrid(x, y)

    dx = X - sus_x
    dy = Y - sus_y
    r = np.sqrt(dx ** 2 + dy ** 2) + 1e-3

    # Dipole-like field perturbation (B0 along y-axis)
    cos_th = dy / r
    delta_field = sus_strength * (3 * cos_th ** 2 - 1) / (r ** 2 + 0.02)

    # Geometric distortion in freq-encode (horizontal) direction
    cols = np.broadcast_to(np.arange(M)[np.newaxis, :], (N, M)).astype(float)
    rows = np.broadcast_to(np.arange(N)[:, np.newaxis], (N, M)).astype(float)
    distorted_cols = np.clip(cols + delta_field * 8.0, 0, M - 1)

    result = map_coordinates(
        image,
        [rows.ravel(), distorted_cols.ravel()],
        order=1, mode='constant', cval=0
    ).reshape(N, M)

    # Signal void: local field gradient magnitude drives dephasing
    grad_x = np.gradient(delta_field, axis=1)
    grad_y = np.gradient(delta_field, axis=0)
    grad_mag = np.sqrt(grad_x ** 2 + grad_y ** 2)
    dephasing = np.exp(-grad_mag * sus_strength * 3.0)
    return result * np.clip(dephasing, 0, 1)


# ── Plot helpers ──────────────────────────────────────────────────────────────

def _image_fig(data, title=''):
    display = np.clip(_norm(data), 0, 1)
    fig = go.Figure()
    fig.add_trace(go.Heatmap(
        z=display,
        colorscale='gray', zmin=0, zmax=1,
        showscale=False,
    ))
    fig.update_layout(
        title=dict(text=title, x=0.5, font_size=13),
        yaxis=dict(scaleanchor='x'),
        plot_bgcolor='rgba(0,0,0,0)',
        margin=go.layout.Margin(l=0, r=0, b=0, t=28),
        autosize=True,
    )
    fig.update_xaxes(showticklabels=False)
    fig.update_yaxes(showticklabels=False)
    return json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)


def game9_worker(phantom_type, artifact_type, params):
    """Compute reference and artifact images.

    Parameters
    ----------
    phantom_type : str
        'shepp-logan' or 'brainweb'
    artifact_type : str
        One of: 'aliasing', 'gibbs', 'motion', 'chemical_shift', 'susceptibility'
    params : dict
        Artifact-specific parameter values.

    Returns
    -------
    ref_json : str
        Plotly JSON for the reference (artifact-free) image.
    artifact_json : str
        Plotly JSON for the image with the artifact applied.
    """
    water = _base_phantom(phantom_type)
    ref_json = _image_fig(water, 'Reference image')

    if artifact_type == 'aliasing':
        artifact = apply_aliasing(water, float(params.get('fov_fraction', 0.7)))
    elif artifact_type == 'gibbs':
        artifact = apply_gibbs(water, float(params.get('kspace_fraction', 25)))
    elif artifact_type == 'motion':
        artifact = apply_motion(
            water,
            float(params.get('motion_amplitude', 0.5)),
            float(params.get('motion_frequency', 3)),
        )
    elif artifact_type == 'chemical_shift':
        fat = _fat_phantom(phantom_type)
        artifact = apply_chemical_shift(
            water, fat, float(params.get('shift_pixels', 10))
        )
    elif artifact_type == 'susceptibility':
        artifact = apply_susceptibility(
            water,
            float(params.get('sus_x', 0.0)),
            float(params.get('sus_y', 0.0)),
            float(params.get('sus_strength', 0.5)),
        )
    else:
        artifact = water.copy()

    artifact_json = _image_fig(artifact, f'{artifact_type.replace("_", " ").title()} artifact')
    return ref_json, artifact_json
