from payment.router import router as pay_router
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from db.database import init_db, engine
from routers.upload   import router as upload_router
from routers.query    import router as query_router
from routers.auth     import router as auth_router
from routers.reduce   import router as reduce_router
from routers.vision   import router as vision_router
from routers.search   import router as search_router
from routers.site     import router as site_router
from routers.admin    import router as admin_router
from routers.write_outline import router as write_outline_router

app = FastAPI(title="PDF 知识库问答系统", version="1.0.0")


def _auto_migrate():
    """启动时检查并补全 users 表缺失的列（幂等）。"""
    from sqlalchemy import text, inspect as sa_inspect
    db_type = engine.dialect.name   # 'postgresql' or 'sqlite'
    try:
        inspector = sa_inspect(engine)
        try:
            existing = {c['name'] for c in inspector.get_columns('users')}
        except Exception:
            return   # 表还不存在，init_db() 会建表
        new_cols = [
            ('email_verified',          'BOOLEAN DEFAULT TRUE'),  # 老用户默认已验证
            ('email_verify_token',      'TEXT'),
            ('email_verify_expires_at', 'TIMESTAMP'),
            ('reset_token',             'TEXT'),
            ('reset_token_expires_at',  'TIMESTAMP'),
        ]
        with engine.begin() as conn:
            for col, defn in new_cols:
                if col in existing:
                    continue
                if db_type == 'postgresql':
                    sql = f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {defn}"
                else:
                    sql = f"ALTER TABLE users ADD COLUMN {col} {defn}"
                try:
                    conn.execute(text(sql))
                    print(f"[migration] ✓ added column: {col}")
                except Exception as e:
                    print(f"[migration] ⚠ {col}: {e}")
    except Exception as e:
        print(f"[migration] error: {e}")


# 初始化数据库并自动迁移
init_db()
_auto_migrate()

# 注册路由
app.include_router(auth_router)
app.include_router(upload_router)
app.include_router(query_router)
app.include_router(reduce_router)
app.include_router(vision_router)
app.include_router(search_router)
app.include_router(pay_router)
app.include_router(site_router)
app.include_router(admin_router)
app.include_router(write_outline_router)

# 静态前端
frontend_dir = Path(__file__).parent / "frontend"
app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

@app.get("/")
def root():
    return FileResponse(str(frontend_dir / "index.html"))

@app.get("/app")
def app_page():
    return FileResponse(str(frontend_dir / "app.html"))

@app.get("/admin")
def admin_page():
    return FileResponse(str(frontend_dir / "admin.html"))

@app.get("/health")
def health():
    return {"status": "ok"}
