from flask import Flask, render_template, redirect, url_for, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename
import os
from models import db, ChatRoom, Message
from datetime import datetime
from flask_socketio import SocketIO, join_room, leave_room, emit


app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///synchat.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5MB limit

ALLOWED_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
ALLOWED_DOC_EXTENSIONS = {'.pdf', '.txt'}

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db.init_app(app)

with app.app_context():
    db.create_all()




socketio = SocketIO(app, cors_allowed_origins="*")
# When user joins a room
@socketio.on('join')
def handle_join(data):
    username = data['username']
    room_id = data['room']
    join_room(room_id)
    emit('status', {'msg': f"{username} has joined the room."}, room=room_id)

# When user sends a message
@socketio.on('send_message')
def handle_message(data):
    username = data['username']
    room_id = data['room']
    msg = data.get('msg', '')
    msg_type = data.get('type', 'text')
    url = data.get('url')
    
    # Save message to DB (optional). Only store text to DB to keep DB small
    if msg_type == 'text' and msg:
        message = Message(room_id=room_id, sender=username, text=msg)
        db.session.add(message)
        db.session.commit()
    
    payload = {'username': username}
    if msg_type == 'image' and url:
        payload.update({'type': 'image', 'url': url})
    else:
        payload.update({'type': 'text', 'msg': msg})
    emit('receive_message', payload, room=room_id)

# When user leaves room
@socketio.on('leave')
def handle_leave(data):
    username = data['username']
    room_id = data['room']
    leave_room(room_id)
    emit('status', {'msg': f"{username} has left the room."}, room=room_id)





@app.route('/')
def index():
    return render_template("index.html")


@app.route('/create')
def create_room():
    # Create a new chat room
    room = ChatRoom()
    db.session.add(room)
    db.session.commit()
    # Redirect to chat page
    return redirect(url_for('chat_room', room_id=room.room_id))



@app.route('/chat/<room_id>')
def chat_room(room_id):
    room = ChatRoom.query.filter_by(room_id=room_id).first()
    if not room:
        return "Room not found!"
    
    # Check expiry
    if datetime.utcnow() > room.expiry_time:
        db.session.delete(room)
        db.session.commit()
        return "This session has expired!"
    
    expiry_ms = int(room.expiry_time.timestamp() * 1000)
    server_now_ms = int(datetime.utcnow().timestamp() * 1000)
    return render_template("chat.html", room_id=room.room_id, expiry_ms=expiry_ms, server_now_ms=server_now_ms)

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/api/rooms/<room_id>', methods=['DELETE'])
def delete_room(room_id):
    room = ChatRoom.query.filter_by(room_id=room_id).first()
    if not room:
        return jsonify({"ok": True, "message": "Room already removed"})
    # Delete messages first
    Message.query.filter_by(room_id=room_id).delete()
    db.session.delete(room)
    db.session.commit()
    # Remove room upload folder if exists
    room_folder = os.path.join(app.config['UPLOAD_FOLDER'], room_id)
    try:
        if os.path.isdir(room_folder):
            for name in os.listdir(room_folder):
                try:
                    os.remove(os.path.join(room_folder, name))
                except Exception:
                    pass
            os.rmdir(room_folder)
    except Exception:
        pass
    return jsonify({"ok": True, "message": "Room and messages deleted"})

@app.route('/api/rooms/<room_id>/upload', methods=['POST'])
def upload_file(room_id):
    room = ChatRoom.query.filter_by(room_id=room_id).first()
    if not room:
        return jsonify({"ok": False, "error": "Room not found"}), 404
    if 'file' not in request.files:
        return jsonify({"ok": False, "error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"ok": False, "error": "No selected file"}), 400
    filename = secure_filename(file.filename)
    _, ext = os.path.splitext(filename)
    lower = ext.lower()
    if lower not in (ALLOWED_IMAGE_EXTENSIONS | ALLOWED_DOC_EXTENSIONS):
        return jsonify({"ok": False, "error": "Unsupported file type"}), 400
    room_folder = os.path.join(app.config['UPLOAD_FOLDER'], room_id)
    os.makedirs(room_folder, exist_ok=True)
    # Unique name
    ts = int(datetime.utcnow().timestamp())
    saved_name = f"{ts}_{filename}"
    save_path = os.path.join(room_folder, saved_name)
    file.save(save_path)
    file_url = url_for('serve_upload', room_id=room_id, filename=saved_name)
    kind = 'image' if lower in ALLOWED_IMAGE_EXTENSIONS else 'file'
    return jsonify({"ok": True, "url": file_url, "type": kind, "filename": filename})

@app.route('/uploads/<room_id>/<path:filename>')
def serve_upload(room_id, filename):
    return send_from_directory(os.path.join(app.config['UPLOAD_FOLDER'], room_id), filename)

# Add this at the end
if __name__ == "__main__":
    socketio.run(app,debug=True, host="0.0.0.0", port=5000,allow_unsafe_werkzeug=True)
