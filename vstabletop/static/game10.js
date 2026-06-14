let socket = io();

$('#link-to-game10').addClass('text-success');

// ── State ─────────────────────────────────────────────────────────────────────

let currentSeq = $('#seq-selector .active').data('seq') || 'gre';

const _plotCfg = { displayModeBar: false, responsive: true };

// ── Sequence selector ─────────────────────────────────────────────────────────

$('.seq-btn').on('click', function () {
    $('.seq-btn').removeClass('active');
    $(this).addClass('active');
    currentSeq = $(this).data('seq');
    // Show/hide TI input
    if (currentSeq === 'ir') {
        $('#ti-group').show();
    } else {
        $('#ti-group').hide();
    }
});

// ── Sidebar tab → switch sequence mode ───────────────────────────────────────

$('.task_tab').on('click', function () {
    const seq = $(this).data('seq');
    if (seq && seq !== 'code') {
        currentSeq = seq;
        $('.seq-btn').removeClass('active');
        $(`.seq-btn[data-seq="${seq}"]`).addClass('active');
        $('#ti-group').toggle(seq === 'ir');
        // Also switch to guided mode tab
        $('#guided-tab').tab('show');
    } else if (seq === 'code') {
        $('#code-tab').tab('show');
    }
});

// ── Collect guided-mode parameters ───────────────────────────────────────────

function getGuidedParams() {
    return {
        te:  parseFloat($('#g10-te').val()) || 6,
        tr:  parseFloat($('#g10-tr').val()) || 25,
        fa:  parseFloat($('#g10-fa').val()) || 30,
        ti:  parseFloat($('#g10-ti').val()) || 100,
        fov: 250,
        thk: 5,
        Nx:  64,
        Ny:  64,
    };
}

// ── Guided RUN ────────────────────────────────────────────────────────────────

$('#g10-run').on('click', () => {
    $('#g10-run').attr('disabled', true);
    $('#g10-spinner').removeClass('d-none');
    socket.emit('Game 10 run guided', {
        seq_type: currentSeq,
        params:   getGuidedParams(),
    });
});

// ── Receive guided results ────────────────────────────────────────────────────

socket.on('Game 10 diagram delivered', (msg) => {
    const diag  = JSON.parse(msg['diagram_json']);
    const ks    = JSON.parse(msg['kspace_json']);
    const code  = msg['code_str'];

    Plotly.newPlot('chart-G10-diagram', diag.data, diag.layout, _plotCfg);
    Plotly.newPlot('chart-G10-kspace',  ks.data,   ks.layout,   _plotCfg);

    guidedEditor.setValue(code);
    userEditor.setValue(code);

    $('#g10-run').attr('disabled', false);
    $('#g10-spinner').addClass('d-none');
});

// ── Copy code button ──────────────────────────────────────────────────────────

$('#copy-code-btn').on('click', () => {
    const code = guidedEditor.getValue();
    navigator.clipboard.writeText(code).then(() => {
        $('#copy-code-btn').text('Copied!');
        setTimeout(() => $('#copy-code-btn').html('<i class="fas fa-copy"></i> Copy'), 1500);
    });
});

// ── Send to editor button ─────────────────────────────────────────────────────

$('#send-to-editor-btn').on('click', () => {
    userEditor.setValue(guidedEditor.getValue());
    $('#code-tab').tab('show');
});

// ── Quick-load buttons ────────────────────────────────────────────────────────

function loadPreset(seqType) {
    const params = getGuidedParams();
    socket.emit('Game 10 run guided', { seq_type: seqType, params: params });
    // Show a loading indicator in the editor
    userEditor.setValue('# Loading ' + seqType.toUpperCase() + ' template...');
    socket.once('Game 10 diagram delivered', (msg) => {
        userEditor.setValue(msg['code_str']);
    });
}

$('#load-gre').on('click', () => loadPreset('gre'));
$('#load-se').on('click',  () => loadPreset('se'));
$('#load-ir').on('click',  () => loadPreset('ir'));

// ── Code editor RUN ───────────────────────────────────────────────────────────

$('#g10-run-code').on('click', () => {
    $('#g10-run-code').attr('disabled', true);
    $('#g10-code-spinner').removeClass('d-none');
    $('#code-stdout').text('Running...');
    $('#code-stderr').text('');
    $('#code-figures').empty();
    socket.emit('Game 10 run code', { code: userEditor.getValue() });
});

// ── Receive code results ──────────────────────────────────────────────────────

socket.on('Game 10 code result', (msg) => {
    $('#g10-run-code').attr('disabled', false);
    $('#g10-code-spinner').addClass('d-none');

    $('#code-stdout').text(msg['stdout'] || '(no output)');
    $('#code-stderr').text(msg['stderr'] || '');

    const figs = msg['figures'] || [];
    const container = $('#code-figures');
    container.empty();
    figs.forEach((b64, i) => {
        container.append(
            `<div class="col-12 col-md-6">
                <div class="card">
                    <div class="card-header py-1 fw-bold">Figure ${i + 1}</div>
                    <div class="card-body p-1 text-center">
                        <img src="data:image/png;base64,${b64}"
                             class="img-fluid" alt="Figure ${i + 1}">
                    </div>
                </div>
            </div>`
        );
    });
});

// ── Stars update ──────────────────────────────────────────────────────────────

socket.on('renew stars', (msg) => {
    const n = msg['stars'];
    const nf = parseInt(Math.floor(n));
    const nh = parseInt(Math.round((n - nf) * 2));
    const ne = 5 - nf - nh;
    let html = '<i class="bi bi-star-fill text-warning"></i> '.repeat(nf)
             + '<i class="bi bi-star-half text-warning"></i> '.repeat(nh)
             + '<i class="bi bi-star text-warning"></i> '.repeat(ne);
    $('#stars-display').html(html);
});

// ── Quiz (MC) ─────────────────────────────────────────────────────────────────

$('.answer-mc').on('click', (event) => {
    const id   = event.target.id;
    const qInd = id[id.length - 1];
    const choice = $(`.q${qInd}-choice:checked`).attr('value');
    const letters = ['a', 'b', 'c', 'd'];
    const corrIdx = parseInt($(`#mc-correct-choice-${qInd}`).text());
    if (choice === letters[corrIdx]) {
        $(`#mc-failure-text-${qInd}`).addClass('d-none');
        $(`#mc-success-text-${qInd}`).removeClass('d-none');
        socket.emit('game 10 question answered', { ind: qInd, correct: true });
    } else {
        $(`#mc-failure-text-${qInd}`).removeClass('d-none');
        $(`#mc-success-text-${qInd}`).addClass('d-none');
        socket.emit('game 10 question answered', { ind: qInd, correct: false });
    }
});

// ── Progress tracking ─────────────────────────────────────────────────────────

$('.task-next-button').click((event) => {
    const tid = event.target.id.replace('task', '').replace('-next', '');
    if ($(`input.task-${tid}-check`).not(':checked').length === 0) {
        socket.emit('game10 update progress', { task: tid });
        $(`#task-message-${tid}`).addClass('d-none');
        $(`#task-success-${tid}`).removeClass('d-none');
        updateProgressBar(tid);
        goToNextTab(parseInt(tid));
    } else {
        if (parseInt(tid) === 4 || $(`#task${parseInt(tid)+1}-tab`).hasClass('disabled')) {
            $(`#task-message-${tid}`).removeClass('d-none');
        } else {
            goToNextTab(parseInt(tid));
        }
    }
});

function updateProgressBar(step) {
    const cur = parseInt($('.progress-bar').attr('style').replace('width: ', '').replace('%', ''));
    const nv  = step * 25;
    if (nv > cur) {
        $('.progress-bar').prop('style', `width: ${nv}%`).prop('aria-valuenow', `${nv}`);
    }
}

function goToNextTab(step) {
    if (step < 4) {
        $(`#task${step + 1}-tab`).removeClass('disabled');
        $(`#task${step + 1}-tab`).tab('show');
        $(`#step${step}`).removeClass('show active');
        $(`#step${step + 1}`).addClass('show active');
    }
}
