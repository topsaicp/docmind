from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from db.database import init_db
from routers.upload   import router as upload_router
from routers.query    import router as query_router
from routers.auth     import router as auth_router
from routers.payment  import router as payment_router
from routers.reduce   import router as reduce_router
from routers.vision   import router as vision_router

app = FastAPI(title="PDF 知识库问答系统", version="1.0.0")

# 初始化数据库
init_db()

# 注册路由
app.include_router(auth_router)
app.include_router(payment_router)
app.include_router(upload_router)
app.include_router(query_router)
app.include_router(reduce_router)
app.include_router(vision_router)

# 静态前端
frontend_dir = Path(__file__).parent / "frontend"
app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

@app.get("/")
def root():
    return FileResponse(str(frontend_dir / "index.html"))

@app.get("/app")
def app_page():
    return FileResponse(str(frontend_dir / "app.html"))

@app.get("/health")
def health():
    return {"status": "ok"}
