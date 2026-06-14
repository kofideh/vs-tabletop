let socket = io();

$('#link-to-game9').addClass('text-success');

// ── Artifact & phantom state ──────────────────────────────────────────────────

let currentArtifact = $('#artifact-selector .active').data('artifact') || 'aliasing';
let currentPhantom  = $('#phantom-selector').val() || 'shepp-logan';

const artifactLabels = {
    aliasing:       'Aliasing (Wrap-around) artifact',
    gibbs:          'Gibbs Ringing artifact',
    motion:         'Motion artifact',
    chemical_shift: 'Chemical Shift artifact',
    susceptibility: 'Susceptibility artifact',
};

// ── Helper: collect all current parameter values ──────────────────────────────

function getParams() {
    return {
        fov_fraction:      parseFloat($('#fov-fraction').val())      || 0.7,
        kspace_fraction:   parseFloat($('#kspace-fraction').val())   || 25,
        motion_amplitude:  parseFloat($('#motion-amplitude').val())  || 0.5,
        motion_frequency:  parseFloat($('#motion-frequency').val())  || 3,
        shift_pixels:      parseFloat($('#shift-pixels').val())      || 10,
        sus_x:             parseFloat($('#sus-x').val())             || 0.0,
        sus_y:             parseFloat($('#sus-y').val())             || 0.0,
        sus_strength:      parseFloat($('#sus-strength').val())      || 0.5,
    };
}

// ── Show/hide parameter panels ────────────────────────────────────────────────

function showParams(artifact) {
    $('.artifact-params').addClass('d-none');
    $(`#params-${artifact}`).removeClass('d-none');
    $('#artifact-header').text(artifactLabels[artifact] || 'Artifact image');
}

showParams(currentArtifact);

// ── Artifact selector buttons ─────────────────────────────────────────────────

$('.artifact-btn').on('click', function () {
    $('.artifact-btn').removeClass('active');
    $(this).addClass('active');
    currentArtifact = $(this).data('artifact');
    showParams(currentArtifact);
});

// ── Phantom selector ──────────────────────────────────────────────────────────

$('#phantom-selector').on('change', function () {
    currentPhantom = $(this).val();
});

// ── Sidebar tab → switch artifact automatically ───────────────────────────────

$('.task_tab').on('click', function () {
    const artifact = $(this).data('artifact');
    if (artifact) {
        currentArtifact = artifact;
        $('.artifact-btn').removeClass('active');
        $(`.artifact-btn[data-artifact="${artifact}"]`).addClass('active');
        showParams(artifact);
    }
});

// ── RUN button ────────────────────────────────────────────────────────────────

$('#game9-run').on('click', () => {
    $('#game9-run').attr('disabled', true);
    $('#game9-spinner').removeClass('d-none');
    socket.emit('Game 9 run', {
        phantom_type:  currentPhantom,
        artifact_type: currentArtifact,
        params:        getParams(),
    });
});

// ── Receive images ────────────────────────────────────────────────────────────

socket.on('Game 9 image delivered', (msg) => {
    const ref      = JSON.parse(msg['ref_json']);
    const artifact = JSON.parse(msg['artifact_json']);
    const cfg      = { displayModeBar: false, responsive: true };
    Plotly.newPlot('chart-G9-ref',      ref.data,      ref.layout,      cfg);
    Plotly.newPlot('chart-G9-artifact', artifact.data, artifact.layout, cfg);
    $('#game9-run').attr('disabled', false);
    $('#game9-spinner').addClass('d-none');
});

// ── Stars update ──────────────────────────────────────────────────────────────

socket.on('renew stars', (msg) => {
    const num_stars = msg['stars'];
    const num_full  = parseInt(Math.floor(num_stars));
    const num_half  = parseInt(Math.round((num_stars - num_full) * 2));
    const num_empty = 5 - num_full - num_half;
    let html = '<i class="bi bi-star-fill text-warning"></i> '.repeat(num_full)
             + '<i class="bi bi-star-half text-warning"></i> '.repeat(num_half)
             + '<i class="bi bi-star text-warning"></i> '.repeat(num_empty);
    $('#stars-display').html(html);
});

// ── Live slider value display ─────────────────────────────────────────────────

$('#fov-fraction').on('input',       () => $('#fov-fraction-val').text(parseFloat($('#fov-fraction').val()).toFixed(2)));
$('#kspace-fraction').on('input',    () => $('#kspace-fraction-val').text($('#kspace-fraction').val()));
$('#motion-amplitude').on('input',   () => $('#motion-amplitude-val').text(parseFloat($('#motion-amplitude').val()).toFixed(2)));
$('#motion-frequency').on('input',   () => $('#motion-frequency-val').text($('#motion-frequency').val()));
$('#shift-pixels').on('input',       () => $('#shift-pixels-val').text($('#shift-pixels').val()));
$('#sus-strength').on('input',       () => $('#sus-strength-val').text(parseFloat($('#sus-strength').val()).toFixed(2)));
$('#sus-x').on('input',              () => $('#sus-x-val').text(parseFloat($('#sus-x').val()).toFixed(2)));
$('#sus-y').on('input',              () => $('#sus-y-val').text(parseFloat($('#sus-y').val()).toFixed(2)));

// ── Popover init ──────────────────────────────────────────────────────────────

$(document).ready(() => {
    $('[data-mdb-toggle="popover"]').popover();
});

// ── Quiz (MC questions) ───────────────────────────────────────────────────────

$('.answer-mc').on('click', (event) => {
    const submit_id = event.target.id;
    const q_ind = submit_id[submit_id.length - 1];
    const choice = $(`.q${q_ind}-choice:checked`).attr('value');
    const letters = ['a', 'b', 'c', 'd'];
    const correct_idx = parseInt($(`#mc-correct-choice-${q_ind}`).text());

    if (choice === letters[correct_idx]) {
        $(`#mc-failure-text-${q_ind}`).addClass('d-none');
        $(`#mc-success-text-${q_ind}`).removeClass('d-none');
        socket.emit('game 9 question answered', { ind: q_ind, correct: true });
    } else {
        $(`#mc-failure-text-${q_ind}`).removeClass('d-none');
        $(`#mc-success-text-${q_ind}`).addClass('d-none');
        socket.emit('game 9 question answered', { ind: q_ind, correct: false });
    }
});

// ── Progress tracking (Next button) ──────────────────────────────────────────

$('.task-next-button').click((event) => {
    const task_id = event.target.id.replace('task', '').replace('-next', '');
    if ($(`input.task-${task_id}-check`).not(':checked').length === 0) {
        socket.emit('game9 update progress', { task: task_id });
        $(`#task-message-${task_id}`).addClass('d-none');
        $(`#task-success-${task_id}`).removeClass('d-none');
        updateProgressBar(task_id);
        goToNextTab(parseInt(task_id));
    } else {
        if (parseInt(task_id) === 5 || $(`#task${parseInt(task_id) + 1}-tab`).hasClass('disabled')) {
            $(`#task-message-${task_id}`).removeClass('d-none');
        } else {
            goToNextTab(parseInt(task_id));
        }
    }
});

function updateProgressBar(step) {
    const current = parseInt($('.progress-bar').attr('style').replace('width: ', '').replace('%', ''));
    const newVal  = step * 20;
    if (newVal > current) {
        $('.progress-bar').prop('style', `width: ${newVal}%`).prop('aria-valuenow', `${newVal}`);
    }
}

function goToNextTab(step) {
    if (step < 5) {
        $(`#task${step + 1}-tab`).removeClass('disabled');
        $(`#task${step + 1}-tab`).tab('show');
        $(`#step${step}`).removeClass('show active');
        $(`#step${step + 1}`).addClass('show active');
    }
}
