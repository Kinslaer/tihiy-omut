from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import sqlite3
import hashlib
import json
from typing import Dict
import os
import base64

app = FastAPI()

# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT UNIQUE NOT NULL,
                  password_hash TEXT NOT NULL,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

init_db()

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

# ========== АВАТАРКИ ==========
AVATAR_DIR = "avatars"
os.makedirs(AVATAR_DIR, exist_ok=True)

@app.post("/upload_avatar/{username}")
async def upload_avatar(username: str, data: dict):
    avatar_data = data.get("avatar", "")
    if avatar_data.startswith("data:image"):
        avatar_data = avatar_data.split(",")[1]
    
    avatar_bytes = base64.b64decode(avatar_data)
    avatar_path = os.path.join(AVATAR_DIR, f"{username}.png")
    with open(avatar_path, "wb") as f:
        f.write(avatar_bytes)
    return {"status": "ok"}

@app.get("/avatar/{username}")
async def get_avatar(username: str):
    avatar_path = os.path.join(AVATAR_DIR, f"{username}.png")
    if os.path.exists(avatar_path):
        with open(avatar_path, "rb") as f:
            return HTMLResponse(content=base64.b64encode(f.read()).decode(), media_type="text/plain")
    return {"error": "no avatar"}

# ========== WEBSOCKET МЕНЕДЖЕР ==========
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.usernames: Dict[WebSocket, str] = {}

    async def connect(self, websocket: WebSocket, username: str):
        await websocket.accept()
        self.active_connections[username] = websocket
        self.usernames[websocket] = username
        await self.broadcast_user_list()

    def disconnect(self, websocket: WebSocket):
        username = self.usernames.pop(websocket, None)
        if username:
            self.active_connections.pop(username, None)
        import asyncio
        asyncio.create_task(self.broadcast_user_list())

    async def send_personal_message(self, message: str, to_username: str):
        if to_username in self.active_connections:
            await self.active_connections[to_username].send_text(message)

    async def broadcast_user_list(self):
        user_list = list(self.active_connections.keys())
        for conn in self.active_connections.values():
            await conn.send_text(json.dumps({"type": "user_list", "users": user_list}))

manager = ConnectionManager()

# ========== API ==========
class UserRegister(BaseModel):
    username: str
    password: str

class UserLogin(BaseModel):
    username: str
    password: str

@app.post("/register")
async def register(user: UserRegister):
    if len(user.username) < 3:
        raise HTTPException(400, "Имя слишком короткое (мин. 3 символа)")
    if len(user.password) < 4:
        raise HTTPException(400, "Пароль слишком короткий (мин. 4 символа)")
    
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    try:
        c.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                  (user.username, hash_password(user.password)))
        conn.commit()
        return {"status": "ok", "message": "Регистрация успешна"}
    except sqlite3.IntegrityError:
        raise HTTPException(400, "Пользователь уже существует")
    finally:
        conn.close()

@app.post("/login")
async def login(user: UserLogin):
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("SELECT id, username FROM users WHERE username=? AND password_hash=?",
              (user.username, hash_password(user.password)))
    result = c.fetchone()
    conn.close()
    
    if result:
        return {"status": "ok", "message": "Вход выполнен", "user_id": result[0], "username": result[1]}
    else:
        raise HTTPException(401, "Неверное имя или пароль")

@app.get("/users")
async def get_all_users():
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("SELECT username FROM users ORDER BY username")
    results = c.fetchall()
    conn.close()
    return {"users": [r[0] for r in results]}

@app.get("/search_users/{query}")
async def search_users(query: str):
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("SELECT username FROM users WHERE username LIKE ? ORDER BY username LIMIT 20", (f"%{query}%",))
    results = c.fetchall()
    conn.close()
    return {"users": [r[0] for r in results]}

# ========== WEBSOCKET ЧАТ ==========
@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    await manager.connect(websocket, username)
    try:
        while True:
            data = await websocket.receive_text()
            message_data = json.loads(data)
            
            if message_data["type"] == "private":
                await manager.send_personal_message(
                    json.dumps({
                        "type": "private",
                        "from": username,
                        "message": message_data["message"],
                        "isImage": message_data.get("isImage", False),
                        "timestamp": message_data.get("timestamp", "")
                    }),
                    message_data["to"]
                )
            elif message_data["type"] == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# ========== СТАТИКА ==========
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
