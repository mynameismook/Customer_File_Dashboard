import os
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, Response
from sqlalchemy import create_engine, Column, Integer, String, DateTime, select, Numeric
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, date
import shutil
import logging
import mimetypes
import urllib.parse
import io
import pandas as pd
import dateutil.parser
from fastapi.staticfiles import StaticFiles # ADDED: Import StaticFiles

# Configuration
# IMPORTANT: Update this path to your desired backup folder
BACKUP_FOLDER = "<-- Verify this path" # <-- Verify this path
os.makedirs(BACKUP_FOLDER, exist_ok=True)

# IMPORTANT: Update this DB URL if your database configuration changes
DB_URL = "your database configuration changes"

# Logging Setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Database Setup
engine = create_engine(DB_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class UploadedFile(Base):
    __tablename__ = "uploaded_files"
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String)
    owner = Column(String)
    work_detail = Column(String, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.now)
    client_ip = Column(String, nullable=True)
    ocr_text = Column(String, nullable=True)
    receipt_date = Column(DateTime, nullable=True)
    total_amount = Column(Numeric(10, 2), nullable=True)
    similarity_status = Column(String, default="No")
    similar_to_file_id = Column(Integer, nullable=True)
    similarity_score = Column(Numeric(5, 2), nullable=True)

# Create database tables (if they don't exist)
Base.metadata.create_all(bind=engine)

app = FastAPI()

origins = [
    "http://localhost",
    "http://localhost:8000",
    "http://localhost:8142", # This allows frontends on port 8142 to connect to this API
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- API Endpoints ---
# IMPORTANT: All API endpoints now have an /api/ prefix

@app.get("/api")
async def read_root():
    return {"message": "Welcome to the File Upload API"}

@app.post("/api/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    owner: str = Form(...),
    work_detail: str = Form(None)
):
    logger.info(f"Received upload request for file: {file.filename}, owner: {owner}, work_detail: {work_detail}")
    try:
        if not file.filename:
            raise HTTPException(status_code=400, detail="No file selected for upload.")

        file_extension = os.path.splitext(file.filename)[1].lower()
        if file_extension not in [".jpg", ".jpeg", ".png", ".pdf"]:
            raise HTTPException(status_code=400, detail="Invalid file type. Only JPG, JPEG, PNG, PDF are allowed.")

        sanitized_filename = "".join(c for c in file.filename if c.isalnum() or c in ('.', '_', '-')).strip()
        if not sanitized_filename:
            sanitized_filename = f"uploaded_file_{datetime.now().strftime('%Y%m%d%H%M%S')}{file_extension}"

        file_path = os.path.join(BACKUP_FOLDER, sanitized_filename)
        logger.info(f"Saving file to: {file_path}")

        os.makedirs(BACKUP_FOLDER, exist_ok=True)

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        client_ip = request.client.host if request.client else None

        db = SessionLocal()
        try:
            new_file = UploadedFile(
                filename=sanitized_filename,
                owner=owner,
                work_detail=work_detail,
                client_ip=client_ip
            )
            db.add(new_file)
            db.commit()
            db.refresh(new_file)
            logger.info(f"File {new_file.filename} uploaded and saved to DB with ID: {new_file.id}")
            return JSONResponse(content={"message": "File uploaded successfully!", "id": new_file.id}, status_code=200)
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to save file info to DB: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to save file info to database: {e}")
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Error during file upload: {e}")
        raise HTTPException(status_code=500, detail=f"An error occurred during upload: {e}")


@app.get("/api/files")
async def get_files(
    owner: str = Query(None),
    filename: str = Query(None),
    receipt_date_start: str = Query(None),
    receipt_date_end: str = Query(None),
    total_amount_min: float = Query(None),
    total_amount_max: float = Query(None),
    similarity_status: str = Query(None)
):
    db = SessionLocal()
    try:
        query = select(UploadedFile)

        if owner:
            query = query.where(UploadedFile.owner == owner)
        if filename:
            query = query.where(UploadedFile.filename.ilike(f"%{filename}%"))

        if receipt_date_start:
            try:
                start_date = dateutil.parser.parse(receipt_date_start).date()
                query = query.where(UploadedFile.receipt_date >= start_date)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid receipt_date_start format. Use YYYY-MM-DD.")
        if receipt_date_end:
            try:
                end_date = dateutil.parser.parse(receipt_date_end).date()
                query = query.where(UploadedFile.receipt_date < (end_date + pd.Timedelta(days=1)).date())
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid receipt_date_end format. Use YYYY-MM-DD.")

        if total_amount_min is not None:
            query = query.where(UploadedFile.total_amount >= total_amount_min)
        if total_amount_max is not None:
            query = query.where(UploadedFile.total_amount <= total_amount_max)

        if similarity_status:
            query = query.where(UploadedFile.similarity_status == similarity_status)

        files = db.execute(query).scalars().all()
        files_data = [{
            "id": f.id,
            "filename": f.filename,
            "owner": f.owner,
            "work_detail": f.work_detail,
            "uploaded_at": f.uploaded_at.isoformat(),
            "client_ip": f.client_ip,
            "ocr_text": f.ocr_text,
            "receipt_date": f.receipt_date.isoformat() if f.receipt_date else None,
            "total_amount": float(f.total_amount) if f.total_amount is not None else None,
            "similarity_status": f.similarity_status,
            "similar_to_file_id": f.similar_to_file_id,
            "similarity_score": float(f.similarity_score) if f.similarity_score is not None else None,
        } for f in files]
        return JSONResponse(content=files_data)
    except Exception as e:
        logger.error(f"Failed to fetch files: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch files: {e}")
    finally:
        db.close()

@app.get("/api/files/{file_id}")
async def get_file_details(file_id: int):
    db = SessionLocal()
    try:
        file = db.execute(select(UploadedFile).where(UploadedFile.id == file_id)).scalars().first()
        if not file:
            raise HTTPException(status_code=404, detail="File not found")

        file_details = {
            "id": file.id,
            "filename": file.filename,
            "owner": file.owner,
            "work_detail": file.work_detail,
            "uploaded_at": file.uploaded_at.isoformat(),
            "client_ip": file.client_ip,
            "ocr_text": file.ocr_text,
            "receipt_date": file.receipt_date.isoformat() if file.receipt_date else None,
            "total_amount": float(file.total_amount) if file.total_amount is not None else None,
            "similarity_status": file.similarity_status,
            "similar_to_file_id": file.similar_to_file_id,
            "similarity_score": float(file.similarity_score) if file.similarity_score is not None else None,
        }
        return JSONResponse(content=file_details)
    except Exception as e:
        logger.error(f"Failed to fetch file details for ID {file_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch file details: {e}")
    finally:
        db.close()

@app.put("/api/files/{file_id}")
async def update_file(
    file_id: int,
    owner: str = Form(...),
    work_detail: str = Form(None),
    ocr_text: str = Form(None),
    receipt_date: str = Form(None),
    total_amount: float = Form(None),
    similarity_status: str = Form(None),
    similar_to_file_id: int = Form(None),
    similarity_score: float = Form(None)
):
    db = SessionLocal()
    try:
        file = db.execute(select(UploadedFile).where(UploadedFile.id == file_id)).scalars().first()
        if not file:
            raise HTTPException(status_code=404, detail="File not found")

        file.owner = owner
        file.work_detail = work_detail
        file.ocr_text = ocr_text
        
        if receipt_date:
            try:
                file.receipt_date = dateutil.parser.parse(receipt_date).date()
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid receipt_date format. Use YYYY-MM-DD.")
        else:
            file.receipt_date = None

        file.total_amount = total_amount
        file.similarity_status = similarity_status
        file.similar_to_file_id = similar_to_file_id
        file.similarity_score = similarity_score
        
        db.commit()
        db.refresh(file)
        logger.info(f"File ID {file_id} updated successfully.")
        return JSONResponse(content={"message": "File updated successfully!", "id": file.id})
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to update file ID {file_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update file: {e}")
    finally:
        db.close()


@app.delete("/api/files/{file_id}")
async def delete_file(file_id: int, owner: str = Query(...)):
    db = SessionLocal()
    try:
        file = db.execute(select(UploadedFile).where(UploadedFile.id == file_id, UploadedFile.owner == owner)).scalars().first()
        if not file:
            raise HTTPException(status_code=404, detail="File not found or owner mismatch.")

        file_path = os.path.join(BACKUP_FOLDER, file.filename)
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"File {file.filename} deleted from disk.")
        else:
            logger.warning(f"File {file.filename} (ID: {file_id}) not found on disk at {file_path}.")

        db.delete(file)
        db.commit()
        logger.info(f"File ID {file_id} deleted from database.")
        return JSONResponse(content={"message": "File deleted successfully!"}, status_code=200)
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to delete file ID {file_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {e}")
    finally:
        db.close()


@app.get("/api/download/{file_id}")
async def download_file(file_id: int):
    db = SessionLocal()
    try:
        file = db.execute(select(UploadedFile).where(UploadedFile.id == file_id)).scalars().first()
        if not file:
            raise HTTPException(status_code=404, detail="File not found.")

        file_path = os.path.join(BACKUP_FOLDER, file.filename)
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="File not found on disk.")

        media_type, _ = mimetypes.guess_type(file_path)
        if not media_type:
            media_type = "application/octet-stream"

        return FileResponse(
            path=file_path,
            filename=file.filename,
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename={urllib.parse.quote(file.filename)}"}
        )
    except Exception as e:
        logger.error(f"Failed to download file ID {file_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to download file: {e}")
    finally:
        db.close()

@app.get("/api/export/csv")
async def export_to_csv():
    db = SessionLocal()
    try:
        files = db.execute(select(UploadedFile)).scalars().all()
        
        if not files:
            return Response(status_code=204)

        data = []
        for file in files:
            data.append({
                "ID": file.id,
                "Filename": file.filename,
                "Owner": file.owner,
                "Work Detail": file.work_detail,
                "Uploaded At": file.uploaded_at.isoformat() if file.uploaded_at else None,
                "Client IP": file.client_ip,
                "OCR Text": file.ocr_text,
                "Receipt Date": file.receipt_date.isoformat() if file.receipt_date else None,
                "Total Amount": float(file.total_amount) if file.total_amount is not None else None,
                "Similarity Status": file.similarity_status,
                "Similar To File ID": file.similar_to_file_id,
                "Similarity Score": float(file.similarity_score) if file.similarity_score is not None else None,
            })
        
        df = pd.DataFrame(data)

        csv_buffer = io.BytesIO()
        df.to_csv(csv_buffer, index=False, encoding='utf-8')
        csv_buffer.seek(0)

        csv_filename = f"uploaded_files_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        return Response(
            content=csv_buffer.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={urllib.parse.quote(csv_filename)}"}
        )
    except Exception as e:
        logger.error(f"Failed to export to CSV: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to export to CSV: {e}")
    finally:
        db.close()

# --- Static Files Mount ---
# IMPORTANT: This must be the LAST route definition in your app.py
# If index.html is in a subfolder (e.g., 'frontend'), change directory="." to directory="frontend"
app.mount("/", StaticFiles(directory=".", html=True), name="static")