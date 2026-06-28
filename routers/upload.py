"""
PDF 上传与处理接口
POST /upload       → 上传PDF，异步处理入库
GET  /documents    → 列出所有文档
DELETE /documents/{doc_id} → 删除文档
GET  /documents/{doc_id}/status → 查询处理状态
"""
import uuid, threading
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from sqlalchemy.orm import Session

from config import UPLOAD_DIR, MAX_FILE_SIZE_MB, ALLOWED_EXT, FREE_PDF_LIMIT
from db.database import get_session, Document, User
from services.pdf_processor import process_pdf
from services.embedder import add_chunks, delete_doc
from routers.auth import get_current_user

router = APIRouter(prefix="/api", tags=["documents"])


def _process_in_background(doc_id: str, pdf_path: str, filename: str):
    """在后台线程中处理PDF并入库"""
    from db.database import Session as DBSession
    session = DBSession()
    doc     = session.query(Document).filter_by(id=doc_id).first()

    try:
        doc.status = "processing"
        session.commit()

        chunks, page_count = process_pdf(pdf_path, doc_id, filename)

        if not chunks:
            raise ValueError("未能从PDF中提取任何文字，可能是不支持的扫描件格式")

        added = add_chunks(chunks)

        doc.status      = "ready"
        doc.page_count  = page_count
        doc.chunk_count = added
        session.commit()
        print(f"✅ {filename} 入库完成：{page_count}页，{added}块")

    except Exception as e:
        doc.status    = "error"
        doc.error_msg = str(e)
        session.commit()
        print(f"❌ {filename} 处理失败：{e}")
    finally:
        session.close()


@router.post("/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    # 免费用户 PDF 数量限制（测试期间暂停）
    # if current_user.plan == "free":
    #     db_user = session.query(User).filter_by(id=current_user.id).first()
    #     if db_user.pdf_count >= FREE_PDF_LIMIT:
    #         raise HTTPException(403, f"免费用户最多上传 {FREE_PDF_LIMIT} 篇 PDF，请升级专业版")

    # 格式校验
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXT:
        raise HTTPException(400, f"仅支持 PDF 文件，收到：{suffix}")

    # 大小校验
    content = await file.read()
    if len(content) > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise HTTPException(400, f"文件超过 {MAX_FILE_SIZE_MB}MB 限制")

    # 保存文件
    doc_id   = str(uuid.uuid4())
    filename = f"{doc_id}{suffix}"
    pdf_path = UPLOAD_DIR / filename
    pdf_path.write_bytes(content)

    # 写入数据库
    doc = Document(
        id            = doc_id,
        user_id       = current_user.id,
        filename      = filename,
        original_name = file.filename,
        size_bytes    = len(content),
        status        = "pending",
    )
    session.add(doc)

    # 更新用户 PDF 计数
    db_user = session.query(User).filter_by(id=current_user.id).first()
    db_user.pdf_count += 1
    session.commit()

    # 后台处理
    threading.Thread(
        target = _process_in_background,
        args   = (doc_id, str(pdf_path), file.filename),
        daemon = True,
    ).start()

    return {
        "doc_id":   doc_id,
        "filename": file.filename,
        "size_mb":  round(len(content) / 1024 / 1024, 2),
        "status":   "pending",
        "message":  "文件已上传，正在后台处理，请稍后查询状态",
    }


@router.get("/documents")
def list_documents(session: Session = Depends(get_session),
                   current_user: User = Depends(get_current_user)):
    docs = session.query(Document).filter_by(user_id=current_user.id).order_by(Document.created_at.desc()).all()
    return [
        {
            "doc_id":        d.id,
            "filename":      d.original_name,
            "size_mb":       round(d.size_bytes / 1024 / 1024, 2),
            "pages":         d.page_count,
            "chunks":        d.chunk_count,
            "status":        d.status,
            "error":         d.error_msg,
            "created_at":    str(d.created_at),
        }
        for d in docs
    ]


@router.get("/documents/{doc_id}/status")
def get_status(doc_id: str, session: Session = Depends(get_session),
               current_user: User = Depends(get_current_user)):
    doc = session.query(Document).filter_by(id=doc_id, user_id=current_user.id).first()
    if not doc:
        raise HTTPException(404, "文档不存在")
    return {
        "doc_id":  doc_id,
        "status":  doc.status,
        "chunks":  doc.chunk_count,
        "error":   doc.error_msg,
    }


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str, session: Session = Depends(get_session),
                    current_user: User = Depends(get_current_user)):
    doc = session.query(Document).filter_by(id=doc_id, user_id=current_user.id).first()
    if not doc:
        raise HTTPException(404, "文档不存在")

    # 删除向量
    deleted_chunks = delete_doc(doc_id)

    # 删除原文件
    pdf_path = UPLOAD_DIR / doc.filename
    if pdf_path.exists():
        pdf_path.unlink()

    # 更新用户 PDF 计数
    db_user = session.query(User).filter_by(id=current_user.id).first()
    if db_user and db_user.pdf_count > 0:
        db_user.pdf_count -= 1

    # 删除数据库记录
    session.delete(doc)
    session.commit()

    return {"message": f"已删除文档及 {deleted_chunks} 个向量块"}
