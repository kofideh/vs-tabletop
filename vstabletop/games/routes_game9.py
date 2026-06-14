from flask import render_template, session
from flask_login import login_required
import vstabletop.utils as utils
from vstabletop.info import GAME9_BACKGROUND, GAME9_INSTRUCTIONS
from vstabletop.models import MultipleChoice
from vstabletop.workers.game9_worker import game9_worker
from .. import socketio
from .routes_game1 import bp_games

_PARAM_KEYS = [
    'fov_fraction', 'kspace_fraction',
    'motion_amplitude', 'motion_frequency',
    'shift_pixels', 'sus_x', 'sus_y', 'sus_strength',
]


@bp_games.route('/9', methods=['GET'])
@login_required
def game9():
    all_Qs = MultipleChoice.query.filter_by(game_number=9).all()
    questions, success_text, uses_images = utils.process_all_game_questions(all_Qs)

    g9 = session['game9']
    params = {k: g9[k] for k in _PARAM_KEYS}
    ref_json, artifact_json = game9_worker(g9['phantom_type'], g9['artifact_type'], params)

    return render_template(
        'games/game9.html',
        template_title='Artifacts!',
        game_num=9,
        background=GAME9_BACKGROUND,
        instructions=GAME9_INSTRUCTIONS,
        questions=questions,
        success_text=success_text,
        uses_images=uses_images,
        graphJSON_ref=ref_json,
        graphJSON_artifact=artifact_json,
    )


@socketio.on('Game 9 run')
def game9_run(msg):
    phantom_type = msg.get('phantom_type', 'shepp-logan')
    artifact_type = msg.get('artifact_type', 'aliasing')
    params = msg.get('params', {})

    updates = {'phantom_type': phantom_type, 'artifact_type': artifact_type}
    for k in _PARAM_KEYS:
        if k in params:
            updates[k] = float(params[k])
    utils.update_session_subdict(session, 'game9', updates)

    ref_json, artifact_json = game9_worker(phantom_type, artifact_type, params)
    socketio.emit('Game 9 image delivered', {
        'ref_json': ref_json,
        'artifact_json': artifact_json,
    })


@socketio.on('game 9 question answered')
def game9_mc_progress(msg):
    status = session['game9']['mc_status_list']
    status[int(msg['ind'])] = bool(msg['correct'])
    utils.update_session_subdict(session, 'game9', {'mc_status_list': status})
    session['game9']['progress'].num_correct = sum(status)
    session['game9']['progress'].update_stars()
    print('Game 9 progress updated:', session['game9']['progress'])
    socketio.emit('renew stars', {'stars': session['game9']['progress'].num_stars})


@socketio.on('game9 update progress')
def game9_update_progress(msg):
    task = int(msg['task'])
    if task > session['game9']['task_completed']:
        utils.update_session_subdict(session, 'game9', {'task_completed': task})
        session['game9']['progress'].num_steps_complete = task
        session['game9']['progress'].update_stars()
        print('Game 9 progress updated:', session['game9']['progress'])
        socketio.emit('renew stars', {'stars': session['game9']['progress'].num_stars})
