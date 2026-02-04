# =====================================================================
# GEVENT MONKEY PATCHING - MUST BE AT THE ABSOLUTE TOP
# Before ANY other imports including standard library
# =====================================================================
from gevent import monkey
monkey.patch_all()

# Now safe to import everything else
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_socketio import SocketIO, emit, join_room, leave_room
from pymongo import MongoClient
from bson import ObjectId
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import os
from datetime import datetime
import secrets

load_dotenv()

app = Flask(__name__, template_folder='templates')
app.secret_key = os.getenv('SECRET_KEY', secrets.token_hex(32))
app.config['MONGO_URI'] = os.getenv('MONGO_URI')

# =====================================================================
# FLASK-SOCKETIO INITIALIZATION (gevent backend)
# =====================================================================
socketio = SocketIO(
    app,
    async_mode='gevent',
    cors_allowed_origins="*",
    logger=False,
    engineio_logger=False
)

# =====================================================================
# GLOBAL STATE FOR LIVE QUIZ ARENA (in-memory, single instance)
# =====================================================================
# Structure: { quiz_id: { user_id: {'username': str, 'sid': str} } }
live_players = {}

# Structure: { quiz_id: { user_id: score } }
live_scores = {}

# Structure: { quiz_id: { 'current_question': int, 'started': bool, 'master_id': str } }
live_quiz_state = {}

# =====================================================================
# LAZY MONGODB CONNECTION
# =====================================================================
client = None
db = None

def get_db():
    global client, db
    if client is None:
        mongo_uri = app.config['MONGO_URI']
        if not mongo_uri:
            raise ValueError("MONGO_URI not set. Check environment variables.")
        client = MongoClient(
            mongo_uri,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            maxPoolSize=50,
            retryWrites=True
        )
        db = client['flux_db']
    return db

def get_collections():
    db = get_db()
    return db['users'], db['quizzes'], db['results']

# =====================================================================
# FLASK-LOGIN SETUP
# =====================================================================
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, id, username, role):
        self.id = str(id)
        self.username = username
        self.role = role

@login_manager.user_loader
def load_user(user_id):
    try:
        users, _, _ = get_collections()
        user_data = users.find_one({'_id': ObjectId(user_id)})
        if user_data:
            return User(str(user_data['_id']), user_data['username'], user_data['role'])
    except Exception as e:
        print(f"Error loading user: {e}")
    return None

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('500.html', error=e), 500

# =====================================================================
# SOCKETIO EVENT HANDLERS
# =====================================================================
@socketio.on('connect')
def handle_connect():
    print(f"[SocketIO] Client connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    print(f"[SocketIO] Client disconnected: {request.sid}")
    # Clean up player from any live quiz rooms
    for quiz_id in list(live_players.keys()):
        players = live_players.get(quiz_id, {})
        for user_id, info in list(players.items()):
            if info.get('sid') == request.sid:
                del live_players[quiz_id][user_id]
                # Broadcast updated player list
                emit('player_list', {
                    'players': [
                        {'user_id': uid, 'username': p['username']}
                        for uid, p in live_players.get(quiz_id, {}).items()
                    ]
                }, room=f"quiz_{quiz_id}")
                print(f"[SocketIO] Removed {info['username']} from quiz {quiz_id}")

@socketio.on('join_lobby')
def handle_join_lobby(data):
    """Handle player joining a live quiz lobby"""
    quiz_id = data.get('quiz_id')
    user_id = data.get('user_id')
    username = data.get('username')
    
    if not quiz_id or not user_id or not username:
        emit('error', {'message': 'Missing required data'})
        return
    
    room = f"quiz_{quiz_id}"
    
    # Initialize quiz in live_players if not exists
    if quiz_id not in live_players:
        live_players[quiz_id] = {}
    
    # Check if this is a rejoin (player was previously in this quiz)
    is_rejoin = user_id in live_players.get(quiz_id, {})
    previous_score = 0
    if quiz_id in live_scores and user_id in live_scores[quiz_id]:
        previous_score = live_scores[quiz_id][user_id]
    
    # Add player to the room
    join_room(room)
    live_players[quiz_id][user_id] = {
        'username': username,
        'sid': request.sid
    }
    
    # Initialize or keep existing score for this player
    if quiz_id not in live_scores:
        live_scores[quiz_id] = {}
    if user_id not in live_scores[quiz_id]:
        live_scores[quiz_id][user_id] = 0
    
    # Check if quiz is already in progress (for rejoin support)
    quiz_state = live_quiz_state.get(quiz_id, {})
    is_quiz_started = quiz_state.get('started', False)
    
    if is_rejoin:
        print(f"[SocketIO] {username} REJOINED quiz {quiz_id} (score: {previous_score})")
    else:
        print(f"[SocketIO] {username} joined lobby for quiz {quiz_id}")
    
    # Broadcast updated player list to everyone in the room
    emit('player_list', {
        'players': [
            {'user_id': uid, 'username': p['username']}
            for uid, p in live_players[quiz_id].items()
        ]
    }, room=room)
    
    # Confirm join to the player with rejoin info
    emit('joined', {
        'message': 'You rejoined the quiz' if is_rejoin else 'You joined the lobby',
        'quiz_id': quiz_id,
        'is_rejoin': is_rejoin,
        'quiz_started': is_quiz_started,
        'current_score': previous_score
    })

@socketio.on('leave_lobby')
def handle_leave_lobby(data):
    """Handle player leaving the lobby"""
    quiz_id = data.get('quiz_id')
    user_id = data.get('user_id')
    
    if quiz_id and user_id:
        room = f"quiz_{quiz_id}"
        leave_room(room)
        
        if quiz_id in live_players and user_id in live_players[quiz_id]:
            username = live_players[quiz_id][user_id].get('username', 'Unknown')
            del live_players[quiz_id][user_id]
            print(f"[SocketIO] {username} left lobby for quiz {quiz_id}")
            
            # Broadcast updated player list
            emit('player_list', {
                'players': [
                    {'user_id': uid, 'username': p['username']}
                    for uid, p in live_players.get(quiz_id, {}).items()
                ]
            }, room=room)

@socketio.on('kick_player')
def handle_kick_player(data):
    """Handle master kicking a player from the lobby"""
    quiz_id = data.get('quiz_id')
    master_id = data.get('master_id')
    target_user_id = data.get('target_user_id')
    
    if not quiz_id or not master_id or not target_user_id:
        emit('error', {'message': 'Missing data'})
        return
    
    # Verify requester is the master
    state = live_quiz_state.get(quiz_id, {})
    if state.get('master_id') != master_id:
        emit('error', {'message': 'Only the quiz master can kick players'})
        return
    
    room = f"quiz_{quiz_id}"
    
    # Find and remove the target player
    if quiz_id in live_players and target_user_id in live_players[quiz_id]:
        player_info = live_players[quiz_id][target_user_id]
        player_sid = player_info.get('sid')
        username = player_info.get('username', 'Unknown')
        
        # Remove from data structures
        del live_players[quiz_id][target_user_id]
        if quiz_id in live_scores and target_user_id in live_scores[quiz_id]:
            del live_scores[quiz_id][target_user_id]
        
        # Notify the kicked player
        if player_sid:
            socketio.emit('kicked', {'message': 'You have been removed from this quiz'}, room=player_sid)
        
        print(f"[SocketIO] {username} was kicked from quiz {quiz_id} by master")
        
        # Broadcast updated player list
        emit('player_list', {
            'players': [
                {'user_id': uid, 'username': p['username']}
                for uid, p in live_players.get(quiz_id, {}).items()
            ]
        }, room=room)
        
        emit('player_kicked', {'username': username}, room=room)

@socketio.on('start_quiz')
def handle_start_quiz(data):
    """Handle master starting the live quiz"""
    quiz_id = data.get('quiz_id')
    user_id = data.get('user_id')
    
    if not quiz_id or not user_id:
        emit('error', {'message': 'Missing data'})
        return
    
    # Verify user is the master
    state = live_quiz_state.get(quiz_id, {})
    if state.get('master_id') != user_id:
        emit('error', {'message': 'Only the quiz master can start the quiz'})
        return
    
    # Check if already started
    if state.get('started'):
        emit('error', {'message': 'Quiz already started'})
        return
    
    # Mark as started
    live_quiz_state[quiz_id]['started'] = True
    live_quiz_state[quiz_id]['current_question'] = 0
    
    room = f"quiz_{quiz_id}"
    print(f"[SocketIO] Quiz {quiz_id} started by master {user_id}")
    
    # Notify all players to redirect to live quiz page
    emit('quiz_started', {
        'quiz_id': quiz_id,
        'message': 'Quiz is starting!'
    }, room=room)
    
    # Start sending questions after a short delay (give time to redirect)
    socketio.start_background_task(send_questions_task, quiz_id)

def send_questions_task(quiz_id):
    """Background task to send questions one by one with timer"""
    import time
    
    _, quizzes_col, _ = get_collections()
    quiz = quizzes_col.find_one({'_id': ObjectId(quiz_id)})
    
    if not quiz:
        print(f"[SocketIO] Quiz {quiz_id} not found")
        return
    
    questions = quiz.get('questions', [])
    room = f"quiz_{quiz_id}"
    
    # Wait for players to load the live quiz page
    socketio.sleep(3)
    
    for idx, question in enumerate(questions):
        # Check if quiz was cancelled
        if quiz_id not in live_quiz_state or not live_quiz_state[quiz_id].get('started'):
            break
        
        live_quiz_state[quiz_id]['current_question'] = idx
        
        # Get time for this specific question (default 30 seconds)
        time_for_question = question.get('time', 30)
        
        # Prepare question data (don't send the answer!)
        question_data = {
            'index': idx,
            'total': len(questions),
            'text': question['text'],
            'type': question['type'],
            'points': question.get('points', 1),
            'time_limit': time_for_question
        }
        
        # Add options for MCQ
        if question['type'] == 'mcq':
            question_data['options'] = question.get('options', [])
        
        print(f"[SocketIO] Sending question {idx + 1}/{len(questions)} for quiz {quiz_id} ({time_for_question}s)")
        
        # Broadcast question to all players
        socketio.emit('new_question', question_data, room=room)
        
        # Wait for this question's time limit
        socketio.sleep(time_for_question)
        
        # Broadcast time up with correct answer revealed
        socketio.emit('question_time_up', {
            'index': idx,
            'correct_answer': question.get('answer'),
            'question_type': question.get('type')
        }, room=room)
        
        # Short pause between questions
        socketio.sleep(2)
    
    # Quiz ended - mark as stopped first, then save results
    if quiz_id in live_quiz_state:
        live_quiz_state[quiz_id]['started'] = False
    
    socketio.emit('quiz_ended', {'quiz_id': quiz_id}, room=room)
    print(f"[SocketIO] Quiz {quiz_id} ended")
    
    # Save results to MongoDB (this also cleans up state)
    save_live_quiz_results(quiz_id, quiz)

def save_live_quiz_results(quiz_id, quiz):
    """Save all player results from live quiz to MongoDB"""
    users_col, _, results_col = get_collections()
    
    scores = live_scores.get(quiz_id, {})
    players = live_players.get(quiz_id, {})
    
    if not scores:
        print(f"[SocketIO] No scores to save for quiz {quiz_id}")
        return
    
    # Calculate total possible points
    total_possible = sum(q.get('points', 1) for q in quiz.get('questions', []))
    
    saved_count = 0
    for user_id, score in scores.items():
        player_info = players.get(user_id, {})
        username = player_info.get('username', 'Unknown')
        
        # Calculate percentage
        percentage = round((score / total_possible * 100), 1) if total_possible > 0 else 0
        
        # Create result document (matching existing results structure)
        result_doc = {
            'quiz_id': ObjectId(quiz_id),
            'quiz_title': quiz.get('title', 'Unknown Quiz'),
            'student_id': ObjectId(user_id),
            'student_name': username,
            'score': score,
            'total_possible': total_possible,
            'percentage': percentage,
            'mode': 'live_arena',  # Mark as live arena result
            'date': datetime.now()
        }
        
        try:
            results_col.insert_one(result_doc)
            saved_count += 1
            print(f"[SocketIO] Saved result for {username}: {score}/{total_possible} ({percentage}%)")
        except Exception as e:
            print(f"[SocketIO] Error saving result for {username}: {e}")
    
    print(f"[SocketIO] Saved {saved_count} results for quiz {quiz_id}")
    
    # Clean up in-memory state for this quiz
    if quiz_id in live_players:
        del live_players[quiz_id]
    if quiz_id in live_scores:
        del live_scores[quiz_id]
    if quiz_id in live_quiz_state:
        del live_quiz_state[quiz_id]

@socketio.on('submit_answer')
def handle_submit_answer(data):
    """Handle player submitting an answer"""
    quiz_id = data.get('quiz_id')
    user_id = data.get('user_id')
    question_index = data.get('question_index')
    answer = data.get('answer', '')
    
    if not all([quiz_id, user_id, question_index is not None]):
        emit('error', {'message': 'Missing data'})
        return
    
    # Get the quiz and question
    _, quizzes_col, _ = get_collections()
    quiz = quizzes_col.find_one({'_id': ObjectId(quiz_id)})
    
    if not quiz:
        emit('error', {'message': 'Quiz not found'})
        return
    
    questions = quiz.get('questions', [])
    if question_index < 0 or question_index >= len(questions):
        emit('error', {'message': 'Invalid question index'})
        return
    
    question = questions[question_index]
    correct_answer = question.get('answer', '')
    q_type = question.get('type', 'mcq')
    points = question.get('points', 1)
    
    # Check correctness based on question type
    is_correct = False
    if q_type == 'tf':
        is_correct = str(correct_answer).strip().upper() == str(answer).strip().upper()
    elif q_type == 'mcq':
        is_correct = str(correct_answer).strip() == str(answer).strip()
    elif q_type == 'short':
        is_correct = str(answer).strip().lower() == str(correct_answer).strip().lower()
    
    # Update score
    if quiz_id not in live_scores:
        live_scores[quiz_id] = {}
    if user_id not in live_scores[quiz_id]:
        live_scores[quiz_id][user_id] = 0
    
    if is_correct:
        live_scores[quiz_id][user_id] += points
        print(f"[SocketIO] {user_id} answered correctly! +{points} points")
    else:
        print(f"[SocketIO] {user_id} answered incorrectly")
    
    room = f"quiz_{quiz_id}"
    
    # Send score update to the player who submitted
    emit('score_update', {
        'user_id': user_id,
        'score': live_scores[quiz_id][user_id],
        'correct': is_correct,
        'points_earned': points if is_correct else 0
    })
    
    # Broadcast live leaderboard to all players
    leaderboard = []
    for uid, score in sorted(live_scores.get(quiz_id, {}).items(), key=lambda x: x[1], reverse=True):
        player_info = live_players.get(quiz_id, {}).get(uid, {})
        leaderboard.append({
            'user_id': uid,
            'username': player_info.get('username', 'Unknown'),
            'score': score
        })
    
    socketio.emit('live_leaderboard', {
        'leaderboard': leaderboard[:10]  # Top 10
    }, room=room)

# =====================================================================
# HTTP ROUTES
# =====================================================================
@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    users, _, _ = get_collections()
    if request.method == 'POST':
        email = request.form['email'].strip()
        password = request.form['password']
        role = request.form['role']
        user_data = users.find_one({'email': email, 'role': role})
        if user_data and check_password_hash(user_data['password'], password):
            user = User(str(user_data['_id']), user_data['username'], user_data['role'])
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Invalid credentials')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    users, _, _ = get_collections()
    if request.method == 'POST':
        username = request.form['username'].strip()
        email = request.form['email'].strip().lower()
        password = generate_password_hash(request.form['password'])
        role = request.form.get('role', 'student')
        if role not in ['student', 'master']:
            role = 'student'

        if users.find_one({'email': email}):
            flash('Email already exists')
            return render_template('register.html')
        if users.find_one({'username': username}):
            flash('Username already taken')
            return render_template('register.html')

        user_id = users.insert_one({
            'username': username,
            'email': email,
            'password': password,
            'role': role
        }).inserted_id
        user = User(str(user_id), username, role)
        login_user(user)
        flash('Registration successful!')
        return redirect(url_for('dashboard'))
    return render_template('register.html')

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html', user=current_user)

@app.route('/create_quiz', methods=['GET', 'POST'])
@login_required
def create_quiz():
    if current_user.role != 'master':
        flash('Access denied')
        return redirect(url_for('dashboard'))

    _, quizzes, _ = get_collections()

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        subject = request.form.get('subject', '').strip()
        try:
            duration = int(request.form.get('duration', 0))
        except ValueError:
            flash('Invalid duration')
            return render_template('create_quiz.html')

        if not title or not subject or duration <= 0:
            flash('Please fill title, subject, and a valid duration')
            return render_template('create_quiz.html')

        questions = []
        for i in range(1, 51):
            q_text = request.form.get(f'q_text_{i}', '').strip()
            if not q_text:
                continue

            q_type = request.form.get(f'q_type_{i}', 'mcq')
            q_answer = request.form.get(f'q_answer_{i}', '').strip()
            try:
                q_points = int(request.form.get(f'q_points_{i}', 1))
            except ValueError:
                q_points = 1

            if q_points < 1:
                q_points = 1
            
            # Parse time per question (seconds)
            try:
                q_time = int(request.form.get(f'q_time_{i}', 30))
            except ValueError:
                q_time = 30
            
            if q_time < 5:
                q_time = 5
            elif q_time > 120:
                q_time = 120

            if q_type == 'tf':
                q_answer = q_answer.upper()
            
            q = {
                'type': q_type,
                'text': q_text,
                'answer': q_answer,
                'points': q_points,
                'time': q_time
            }

            if q_type == 'mcq':
                options = []
                for j in range(1, 5):
                    opt = request.form.get(f'option_{i}_{j}', '').strip()
                    if opt:
                        options.append(opt)
                if len(options) < 2:
                    flash(f'MCQ Question {i} needs at least 2 options')
                    return render_template('create_quiz.html')
                q['options'] = options
            
            questions.append(q)

        if len(questions) == 0:
            flash('Add at least one question')
            return render_template('create_quiz.html')
        if len(questions) > 50:
            flash('Maximum 50 questions allowed')
            return render_template('create_quiz.html')

        try:
            quizzes.insert_one({
                'title': title,
                'subject': subject,
                'duration': duration,
                'questions': questions,
                'createdBy': ObjectId(current_user.id),
                'date': datetime.now()
            })
            flash(f'Quiz "{title}" created successfully!')
            return redirect(url_for('quizzes'))
        except Exception as e:
            flash('Quiz creation failed – please try again')
            print(f"DB Error: {e}")

    return render_template('create_quiz.html')

@app.route('/quizzes')
@login_required
def quizzes():
    _, quizzes_col, _ = get_collections()
    if current_user.role == 'master':
        quiz_list = list(quizzes_col.find({'createdBy': ObjectId(current_user.id)}).sort('date', -1))
    else:
        quiz_list = list(quizzes_col.find({}).sort('date', -1))
    
    for quiz in quiz_list:
        quiz['_id'] = str(quiz['_id'])
        
    return render_template('quizzes.html', quizzes=quiz_list, user=current_user)

# =====================================================================
# LIVE QUIZ ARENA ROUTES
# =====================================================================
@app.route('/lobby/<quiz_id>')
@login_required
def lobby(quiz_id):
    """Live quiz lobby - master can start, students can join"""
    _, quizzes_col, _ = get_collections()
    
    try:
        quiz = quizzes_col.find_one({'_id': ObjectId(quiz_id)})
    except:
        flash('Invalid quiz ID')
        return redirect(url_for('quizzes'))
    
    if not quiz:
        flash('Quiz not found')
        return redirect(url_for('quizzes'))
    
    # Check if user is the master (creator) of this quiz
    is_master = str(quiz.get('createdBy')) == current_user.id
    
    # Initialize quiz state if not exists
    if quiz_id not in live_quiz_state:
        live_quiz_state[quiz_id] = {
            'started': False,
            'current_question': 0,
            'master_id': str(quiz.get('createdBy'))
        }
    
    quiz['_id'] = str(quiz['_id'])
    
    return render_template('lobby.html', 
                           quiz=quiz, 
                           user=current_user, 
                           is_master=is_master,
                           quiz_state=live_quiz_state.get(quiz_id, {}))

@app.route('/live_quiz/<quiz_id>')
@login_required
def live_quiz(quiz_id):
    """Live quiz gameplay page - receives real-time questions"""
    _, quizzes_col, _ = get_collections()
    
    try:
        quiz = quizzes_col.find_one({'_id': ObjectId(quiz_id)})
    except:
        flash('Invalid quiz ID')
        return redirect(url_for('quizzes'))
    
    if not quiz:
        flash('Quiz not found')
        return redirect(url_for('quizzes'))
    
    # Check if quiz has started
    state = live_quiz_state.get(quiz_id, {})
    if not state.get('started'):
        flash('This quiz has not started yet')
        return redirect(url_for('lobby', quiz_id=quiz_id))
    
    is_master = str(quiz.get('createdBy')) == current_user.id
    quiz['_id'] = str(quiz['_id'])
    
    return render_template('live_quiz.html', 
                           quiz=quiz, 
                           user=current_user, 
                           is_master=is_master)

@app.route('/arena_standings/<quiz_id>')
@login_required
def arena_standings(quiz_id):
    """Final standings/podium page after live quiz ends"""
    _, quizzes_col, results_col = get_collections()
    
    try:
        quiz = quizzes_col.find_one({'_id': ObjectId(quiz_id)})
    except:
        flash('Invalid quiz ID')
        return redirect(url_for('quizzes'))
    
    if not quiz:
        flash('Quiz not found')
        return redirect(url_for('quizzes'))
    
    # Get all results for this quiz from live arena
    results = list(results_col.find({
        'quiz_id': ObjectId(quiz_id),
        'mode': 'live_arena'
    }).sort('score', -1))
    
    # Calculate total possible
    total_possible = sum(q.get('points', 1) for q in quiz.get('questions', []))
    
    # Prepare standings data
    standings = []
    for idx, res in enumerate(results):
        standings.append({
            'rank': idx + 1,
            'username': res.get('student_name', 'Unknown'),
            'score': res.get('score', 0),
            'total_possible': total_possible,
            'percentage': res.get('percentage', 0),
            'user_id': str(res.get('student_id', ''))
        })
    
    # Get top 3 for podium
    podium = standings[:3] if len(standings) >= 3 else standings
    
    is_master = str(quiz.get('createdBy')) == current_user.id
    quiz['_id'] = str(quiz['_id'])
    
    return render_template('arena_standings.html',
                           quiz=quiz,
                           podium=podium,
                           standings=standings,
                           user=current_user,
                           is_master=is_master)

@app.route('/arena_history')
@login_required
def arena_history():
    """Show history of all arena sessions"""
    _, quizzes_col, results_col = get_collections()
    
    # Get unique quiz IDs from live arena results
    pipeline = [
        {'$match': {'mode': 'live_arena'}},
        {'$group': {
            '_id': '$quiz_id',
            'sessions': {'$push': {
                'student_name': '$student_name',
                'score': '$score',
                'percentage': '$percentage',
                'date': '$date'
            }},
            'player_count': {'$sum': 1},
            'avg_score': {'$avg': '$percentage'},
            'last_played': {'$max': '$date'}
        }},
        {'$sort': {'last_played': -1}},
        {'$limit': 50}
    ]
    
    arena_sessions = list(results_col.aggregate(pipeline))
    
    # Enrich with quiz details
    for session in arena_sessions:
        quiz = quizzes_col.find_one({'_id': session['_id']})
        if quiz:
            session['quiz_title'] = quiz.get('title', 'Unknown Quiz')
            session['quiz_subject'] = quiz.get('subject', 'N/A')
            session['question_count'] = len(quiz.get('questions', []))
            if current_user.role == 'master':
                # Masters see all sessions for their quizzes
                session['can_view'] = str(quiz.get('createdBy')) == current_user.id
            else:
                # Students see sessions they participated in
                session['can_view'] = any(
                    s.get('student_name') == current_user.username 
                    for s in session.get('sessions', [])
                )
        else:
            session['quiz_title'] = 'Deleted Quiz'
            session['quiz_subject'] = 'N/A'
            session['question_count'] = 0
            session['can_view'] = False
        
        session['_id'] = str(session['_id'])
    
    # Filter to only show relevant sessions
    arena_sessions = [s for s in arena_sessions if s.get('can_view', False)]
    
    return render_template('arena_history.html',
                           sessions=arena_sessions,
                           user=current_user)

@app.route('/take_quiz/<quiz_id>')
@login_required
def take_quiz(quiz_id):
    if current_user.role != 'student':
        flash('Access denied')
        return redirect(url_for('dashboard'))

    _, quizzes_col, _ = get_collections()
    try:
        quiz = quizzes_col.find_one({'_id': ObjectId(quiz_id)})
    except:
        flash('Invalid quiz ID')
        return redirect(url_for('quizzes'))

    if not quiz:
        flash('Quiz not found')
        return redirect(url_for('quizzes'))

    quiz['duration_seconds'] = int(quiz['duration']) * 60
    quiz['_id'] = str(quiz['_id'])
    return render_template('take_quiz.html', quiz=quiz)

@app.route('/submit_quiz/<quiz_id>', methods=['POST'])
@login_required
def submit_quiz(quiz_id):
    if current_user.role != 'student':
        return redirect(url_for('dashboard'))

    _, quizzes_col, results_col = get_collections()
    try:
        quiz = quizzes_col.find_one({'_id': ObjectId(quiz_id)})
    except:
        return redirect(url_for('quizzes'))

    if not quiz:
        flash('Quiz not found')
        return redirect(url_for('quizzes'))

    answers = {}
    for i, q in enumerate(quiz['questions']):
        q_key = f"q_{i+1}"
        ans = request.form.get(q_key, '').strip()
        answers[q_key] = ans

    score = 0
    total = sum(q['points'] for q in quiz['questions'])
    scored_answers = []
    
    for i, q in enumerate(quiz['questions']):
        q_key = f"q_{i+1}"
        ans = answers.get(q_key, '')
        
        correct = False
        if q['type'] == 'tf':
            correct = str(q['answer']).strip().upper() == str(ans).strip().upper()
        elif q['type'] == 'mcq':
            correct = str(q['answer']).strip() == str(ans).strip()
        elif q['type'] == 'short':
            correct = ans.lower() == q['answer'].lower()
            
        if correct:
            score += q['points']
            
        scored_answers.append({
            'question': q['text'],
            'student_answer': ans,
            'correct_answer': q['answer'],
            'correct': correct,
            'type': q['type'],
            'points': q['points']
        })

    percentage = round((score / total * 100), 2) if total > 0 else 0

    results_col.insert_one({
        'user': ObjectId(current_user.id),
        'username': current_user.username,
        'quiz': ObjectId(quiz_id),
        'answers': scored_answers,
        'score': score,
        'total': total,
        'percentage': percentage,
        'date': datetime.now()
    })

    return render_template('results.html', score=score, total=total, percentage=percentage,
                           answers=scored_answers, quiz_title=quiz['title'])

@app.route('/my_results')
@login_required
def my_results():
    users, quizzes_col, results_col = get_collections()

    if current_user.role == 'student':
        # Find results for both solo quiz ('user') and live arena ('student_id')
        raw_results = list(results_col.find({
            '$or': [
                {'user': ObjectId(current_user.id)},
                {'student_id': ObjectId(current_user.id)}
            ]
        }).sort('date', -1))
    else:
        raw_results = list(results_col.find({}).sort('date', -1))

    enriched_results = []
    for res in raw_results:
        # Handle both field naming conventions
        quiz_id_field = res.get('quiz') or res.get('quiz_id')
        user_id_field = res.get('user') or res.get('student_id')
        
        quiz = quizzes_col.find_one({'_id': quiz_id_field}) if quiz_id_field else None
        student = users.find_one({'_id': user_id_field}) if user_id_field else None

        # Get student name from result (live arena) or from user lookup
        student_name = res.get('student_name') or (student['username'] if student else 'Unknown')
        
        # Get total from result - handle both 'total' and 'total_possible'
        total = res.get('total') or res.get('total_possible', 0)
        
        # Get mode (solo or live_arena)
        mode = res.get('mode', 'solo')

        enriched_results.append({
            '_id': str(res['_id']),
            'score': res['score'],
            'total': total,
            'percentage': res.get('percentage', 0),
            'date': res['date'],
            'quiz_title': res.get('quiz_title') or (quiz['title'] if quiz else 'Deleted Quiz'),
            'quiz_subject': quiz['subject'] if quiz else '',
            'student_name': student_name,
            'mode': mode
        })

    return render_template('my_results.html', results=enriched_results, user=current_user)

@app.route('/leaderboard')
@login_required
def leaderboard():
    users, _, results_col = get_collections()

    pipeline = [
        {'$match': {'score': {'$exists': True}}},
        {'$group': {
            '_id': '$user',
            'totalScore': {'$sum': '$score'}
        }},
        {'$sort': {'totalScore': -1}},
        {'$limit': 10}
    ]
    
    try:
        agg_results = list(results_col.aggregate(pipeline))
    except Exception as e:
        print(f"Aggregation Error: {e}")
        agg_results = []

    top_users = []
    for agg in agg_results:
        uid = agg['_id']
        username = 'Unknown Student'
        if uid:
            try:
                u = users.find_one({'_id': ObjectId(uid)})
                if u: 
                    username = u.get('username', 'Unknown')
            except:
                pass
        
        top_users.append({
            'username': username,
            'score': agg['totalScore']
        })
        
    return render_template('leaderboard.html', leaderboard=top_users)

@app.route('/edit_quiz/<quiz_id>', methods=['GET', 'POST'])
@login_required
def edit_quiz(quiz_id):
    if current_user.role != 'master':
        flash('Access denied')
        return redirect(url_for('dashboard'))

    _, quizzes_col, _ = get_collections()
    try:
        quiz = quizzes_col.find_one({'_id': ObjectId(quiz_id), 'createdBy': ObjectId(current_user.id)})
    except:
        flash('Invalid Quiz ID')
        return redirect(url_for('quizzes'))
        
    if not quiz:
        flash('Quiz not found or access denied')
        return redirect(url_for('quizzes'))

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        subject = request.form.get('subject', '').strip()
        try:
            duration = int(request.form.get('duration', 0))
        except ValueError:
            flash('Invalid duration')
            return render_template('edit_quiz.html', quiz=quiz)

        if not title or not subject or duration <= 0:
            flash('Please fill title, subject, and valid duration')
            return render_template('edit_quiz.html', quiz=quiz)

        questions = []
        for i in range(1, 51):
            q_text = request.form.get(f'q_text_{i}', '').strip()
            if not q_text:
                continue

            q_type = request.form.get(f'q_type_{i}', 'mcq')
            q_answer = request.form.get(f'q_answer_{i}', '').strip()
            try:
                q_points = int(request.form.get(f'q_points_{i}', 1))
            except ValueError:
                q_points = 1
            if q_points < 1:
                q_points = 1

            if q_type == 'tf':
                q_answer = q_answer.upper()

            q = {
                'type': q_type,
                'text': q_text,
                'answer': q_answer,
                'points': q_points
            }

            if q_type == 'mcq':
                options = []
                for j in range(1, 5):
                    opt = request.form.get(f'option_{i}_{j}', '').strip()
                    if opt:
                        options.append(opt)
                if len(options) < 2:
                    flash(f'MCQ Question {i} needs at least 2 options')
                    return render_template('edit_quiz.html', quiz=quiz)
                q['options'] = options
            
            questions.append(q)

        if len(questions) == 0:
            flash('Add at least one question')
            return render_template('edit_quiz.html', quiz=quiz)

        try:
            quizzes_col.update_one(
                {'_id': ObjectId(quiz_id)},
                {'$set': {
                    'title': title,
                    'subject': subject,
                    'duration': duration,
                    'questions': questions,
                    'date': datetime.now()
                }}
            )
            flash('Quiz updated successfully!')
            return redirect(url_for('quizzes'))
        except Exception as e:
            flash('Update failed – please try again')
            print(f"DB Error: {e}")

    quiz['_id'] = str(quiz['_id'])
    return render_template('edit_quiz.html', quiz=quiz)

@app.route('/delete_quiz/<quiz_id>', methods=['GET', 'POST'])
@login_required
def delete_quiz(quiz_id):
    if current_user.role != 'master':
        flash('Access denied')
        return redirect(url_for('dashboard'))

    _, quizzes_col, _ = get_collections()
    try:
        quiz = quizzes_col.find_one({'_id': ObjectId(quiz_id), 'createdBy': ObjectId(current_user.id)})
    except:
        return redirect(url_for('quizzes'))
        
    if quiz:
        quizzes_col.delete_one({'_id': ObjectId(quiz_id)})
        flash('Quiz deleted successfully!')
    return redirect(url_for('quizzes'))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# =====================================================================
# RUN WITH SOCKETIO (gevent backend)
# =====================================================================
if __name__ == '__main__':
    print("[FLUX] Starting server with gevent backend...")
    socketio.run(app, debug=False, host='0.0.0.0', port=int(os.getenv('PORT', 5000)))
