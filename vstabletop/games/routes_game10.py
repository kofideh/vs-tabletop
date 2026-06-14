from flask import render_template, session
from flask_login import login_required
import vstabletop.utils as utils
from vstabletop.info import GAME10_BACKGROUND, GAME10_INSTRUCTIONS
from vstabletop.models import MultipleChoice
from vstabletop.workers.game10_worker import game10_worker, run_user_code, make_code
from .. import socketio
from .routes_game1 import bp_games

_PARAM_KEYS = ['te', 'tr', 'fa', 'ti', 'fov', 'thk', 'Nx', 'Ny']


@bp_games.route('/10', methods=['GET'])
@login_required
def game10():
    all_Qs = MultipleChoice.query.filter_by(game_number=10).all()
    questions, success_text, uses_images = utils.process_all_game_questions(all_Qs)

    g10 = session['game10']
    params = {k: g10[k] for k in _PARAM_KEYS}
    diagram_json, kspace_json, code_str = game10_worker(g10['seq_type'], params)

    return render_template(
        'games/game10.html',
        template_title='Pulse Programmer',
        game_num=10,
        background=GAME10_BACKGROUND,
        instructions=GAME10_INSTRUCTIONS,
        questions=questions,
        success_text=success_text,
        uses_images=uses_images,
        graphJSON_diagram=diagram_json,
        graphJSON_kspace=kspace_json,
        init_code=code_str,
    )


@socketio.on('Game 10 run guided')
def game10_run_guided(msg):
    seq_type = msg.get('seq_type', 'gre')
    params   = msg.get('params', {})

    updates = {'seq_type': seq_type}
    for k in _PARAM_KEYS:
        if k in params:
            try:
                updates[k] = float(params[k]) if k not in ('Nx', 'Ny') else int(params[k])
            except (ValueError, TypeError):
                pass
    utils.update_session_subdict(session, 'game10', updates)

    p = {k: session['game10'][k] for k in _PARAM_KEYS}
    diagram_json, kspace_json, code_str = game10_worker(seq_type, p)
    socketio.emit('Game 10 diagram delivered', {
        'diagram_json': diagram_json,
        'kspace_json':  kspace_json,
        'code_str':     code_str,
    })


@socketio.on('Game 10 run code')
def game10_run_code(msg):
    user_code = msg.get('code', '')
    utils.update_session_subdict(session, 'game10', {'user_code': user_code})
    stdout, stderr, figures = run_user_code(user_code, timeout=15)
    socketio.emit('Game 10 code result', {
        'stdout':  stdout,
        'stderr':  stderr,
        'figures': figures,
    })


@socketio.on('game 10 question answered')
def game10_mc_progress(msg):
    status = session['game10']['mc_status_list']
    status[int(msg['ind'])] = bool(msg['correct'])
    utils.update_session_subdict(session, 'game10', {'mc_status_list': status})
    session['game10']['progress'].num_correct = sum(status)
    session['game10']['progress'].update_stars()
    socketio.emit('renew stars', {'stars': session['game10']['progress'].num_stars})


@socketio.on('game10 update progress')
def game10_update_progress(msg):
    task = int(msg['task'])
    if task > session['game10']['task_completed']:
        utils.update_session_subdict(session, 'game10', {'task_completed': task})
        session['game10']['progress'].num_steps_complete = task
        session['game10']['progress'].update_stars()
        socketio.emit('renew stars', {'stars': session['game10']['progress'].num_stars})
