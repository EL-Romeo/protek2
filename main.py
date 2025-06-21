import sys
import os
from fastapi import FastAPI, HTTPException, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional, List, Dict
from pydantic import BaseModel
import shutil
import pandas as pd
import io
import sqlite3
import logging
import configparser
from datetime import datetime
from fastapi.responses import FileResponse, StreamingResponse

# --- KODE TAMBAHAN UNTUK MENANGANI PATH SAAT MENJADI .EXE ---
def get_base_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = get_base_path()
DB_PATH = os.path.join(BASE_DIR, 'vehicle_data.db')
CONFIG_PATH = os.path.join(BASE_DIR, 'config.ini')
# --- AKHIR DARI KODE TAMBAHAN ---


# --- Data Driver Awal (Hanya untuk inisialisasi database pertama kali) ---
INITIAL_DRIVERS_DATA = [
    {"name": "PRAPTO", "plate": "AE 9481 BD", "fuel_type": "Solar", "Jenis": "L-300"},
    {"name": "ARDA", "plate": "AE 9482 BD", "fuel_type": "Solar", "Jenis": "L-300"},
    # ... (sisa data driver Anda) ...
    {"name": "UDIN", "plate": "AE 4003 BO", "fuel_type": "Pertalite", "Jenis": "VIAR"},
]

# --- Kelas Database Manager ---
class DatabaseManager:
    def __init__(self, db_path):
        self.db_path = db_path
        self.backup_dir = os.path.join(BASE_DIR, "database_backups") 
        os.makedirs(self.backup_dir, exist_ok=True)
        self.logger = logging.getLogger(__name__)
        self._initialize_tables()

    # ... (sisa kelas DatabaseManager Anda, tidak perlu diubah) ...
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _initialize_tables(self):
        with self._get_connection() as conn:
            c = conn.cursor()
            c.execute('''
                CREATE TABLE IF NOT EXISTS drivers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    plate TEXT NOT NULL UNIQUE,
                    Jenis TEXT,
                    fuel_type TEXT
                )
            ''')
            c.execute('''
                CREATE TABLE IF NOT EXISTS fuel_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    plate TEXT NOT NULL, 
                    fuel_cost REAL DEFAULT 0,
                    kilometer REAL DEFAULT 0,
                    fuel_type TEXT,
                    granit REAL DEFAULT 0,
                    keramik REAL DEFAULT 0,
                    service_type TEXT DEFAULT '',
                    service_cost REAL DEFAULT 0,
                    driver_id INTEGER,
                    FOREIGN KEY (driver_id) REFERENCES drivers (id) ON DELETE CASCADE
                )
            ''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_driver_id_date ON fuel_data (driver_id, date)')
            conn.commit()
            self._seed_initial_drivers()
            
    def _seed_initial_drivers(self):
        with self._get_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM drivers")
            if c.fetchone()[0] == 0:
                c.executemany(
                    "INSERT INTO drivers (name, plate, Jenis, fuel_type) VALUES (:name, :plate, :Jenis, :fuel_type)",
                    INITIAL_DRIVERS_DATA
                )
                conn.commit()

    def create_backup(self, backup_type="manual", force=False):
        today_str = datetime.now().strftime("%Y%m%d")
        
        if backup_type == "auto_daily" and not force:
            expected_filename = f"auto_daily_backup_{today_str}.db"
            backup_path = os.path.join(self.backup_dir, expected_filename)
            if os.path.exists(backup_path):
                return None 
            backup_name = expected_filename
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{backup_type}_backup_{timestamp}.db"
        
        backup_path = os.path.join(self.backup_dir, backup_name)
        shutil.copy(self.db_path, backup_path)
        self.logger.info(f"Backup created at {backup_path}")
        return backup_path

    def restore_database(self, backup_path):
        if not os.path.exists(backup_path) or not backup_path.endswith('.db'):
            raise ValueError("File backup tidak valid.")
        self.create_backup("pre_restore_rescue")
        shutil.copy(backup_path, self.db_path)
        self.logger.info(f"Database restored from {backup_path}")

    def fetch_drivers(self):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT id, name, plate, Jenis, fuel_type FROM drivers ORDER BY id")
            return [dict(row) for row in c.fetchall()]

    def update_drivers(self, drivers: List[Dict]):
        with self._get_connection() as conn:
            c = conn.cursor()
            c.execute("BEGIN TRANSACTION")
            try:
                c.execute("SELECT id FROM drivers")
                existing_ids = {row[0] for row in c.fetchall()}
                
                submitted_ids = {d['id'] for d in drivers if d.get('id') is not None}
                
                for driver in drivers:
                    if driver.get('id') in existing_ids:
                        c.execute(
                            "UPDATE drivers SET name=?, plate=?, Jenis=?, fuel_type=? WHERE id=?",
                            (driver['name'], driver['plate'], driver['Jenis'], driver['fuel_type'], driver['id'])
                        )
                    else:
                        c.execute(
                            "INSERT INTO drivers (name, plate, Jenis, fuel_type) VALUES (?, ?, ?, ?)",
                            (driver['name'], driver['plate'], driver['Jenis'], driver['fuel_type'])
                        )
                
                ids_to_delete = existing_ids - submitted_ids
                if ids_to_delete:
                    c.executemany("DELETE FROM drivers WHERE id=?", [(id,) for id in ids_to_delete])

                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e

    def insert_data(self, data):
        with self._get_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT INTO fuel_data (driver_id, date, plate, fuel_cost, kilometer, fuel_type, granit, keramik, service_type, service_cost) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (data['driver_id'], data['date'], data['plate'], data['fuel_cost'], data['kilometer'], data['fuel_type'], data['granit'], data['keramik'], data['service_type'], data['service_cost']))
            conn.commit()
            return c.lastrowid

    def fetch_data(self, driver_id: int, start_date=None, end_date=None):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            query = 'SELECT * FROM fuel_data WHERE driver_id = ?'
            params = [driver_id]
            if start_date and end_date:
                query += " AND date BETWEEN ? AND ?"
                params.extend([start_date, end_date])
            query += " ORDER BY date DESC"
            c.execute(query, params)
            return [dict(row) for row in c.fetchall()]

    def update_data(self, id, data):
        with self._get_connection() as conn:
            c = conn.cursor()
            set_clauses = [f"{field} = ?" for field in data.keys()]
            params = list(data.values()) + [id]
            query = f"UPDATE fuel_data SET {', '.join(set_clauses)} WHERE id = ?"
            c.execute(query, params)
            conn.commit()
            return c.rowcount

    def delete_data(self, id):
        with self._get_connection() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM fuel_data WHERE id = ?", (id,))
            conn.commit()
            return c.rowcount

# --- Inisialisasi Aplikasi ---
app = FastAPI(title="API Manajemen Kendaraan NEO")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
db_manager = DatabaseManager(DB_PATH) # Gunakan path absolut

# --- Model Pydantic ---
class Driver(BaseModel):
    id: Optional[int] = None
    name: str
    plate: str
    Jenis: str
    fuel_type: str

class FuelDataCreate(BaseModel):
    driver_id: int
    date: str
    plate: str
    fuel_cost: float = 0
    kilometer: float = 0
    fuel_type: str
    granit: float = 0
    keramik: float = 0
    service_type: Optional[str] = ""
    service_cost: float = 0

class FuelDataUpdate(BaseModel):
    date: Optional[str] = None
    fuel_cost: Optional[float] = None
    kilometer: Optional[float] = None
    fuel_type: Optional[str] = None
    granit: Optional[float] = None
    keramik: Optional[float] = None
    service_type: Optional[str] = None
    service_cost: Optional[float] = None

# --- Fungsi Utilitas ---
def get_fuel_prices():
    config = configparser.ConfigParser()
    config.read(CONFIG_PATH) # Gunakan path absolut
    prices = {}
    if 'FuelPrices' in config:
        for key, value in config['FuelPrices'].items():
            prices[key.capitalize()] = float(value)
    return prices
    
# ... (sisa endpoint API Anda sama persis) ...
# Endpoint untuk Manajemen Kendaraan
@app.get("/api/vehicles", tags=["Vehicles"])
def get_all_vehicles():
    """Mengambil semua data driver dari database."""
    return {"vehicles": db_manager.fetch_drivers()}

@app.post("/api/vehicles/update", tags=["Vehicles"])
def update_all_vehicles(drivers: List[Driver]):
    """Memperbarui, menambah, atau menghapus data driver."""
    try:
        driver_dicts = [d.dict() for d in drivers]
        db_manager.update_drivers(driver_dicts)
        db_manager.create_backup(backup_type="auto_daily")
        return {"detail": "Data driver berhasil diperbarui."}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Gagal: Plat nomor duplikat terdeteksi.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Endpoint untuk Data dan Statistik
@app.get("/api/drivers/{driver_id}/data", tags=["Data"])
def get_vehicle_data(driver_id: int, start_date: Optional[str] = None, end_date: Optional[str] = None):
    """Mengambil data riwayat untuk satu driver berdasarkan rentang tanggal."""
    return db_manager.fetch_data(driver_id, start_date, end_date)

@app.get("/api/drivers/{driver_id}/stats", tags=["Data"])
def get_vehicle_stats(driver_id: int, start_date: Optional[str] = None, end_date: Optional[str] = None):
    """Menghitung dan mengambil data statistik untuk satu driver."""
    raw_data = db_manager.fetch_data(driver_id, start_date, end_date)
    df = pd.DataFrame(raw_data)
    
    if df.empty: 
        return {"total_cost": 0, "total_distance": 0, "avg_consumption": 0, "total_service_cost": 0, "total_liters": 0}

    fuel_prices = get_fuel_prices()
    
    total_fuel_cost = df['fuel_cost'].sum()
    total_service_cost = df['service_cost'].sum()
    
    sorted_df = df[df['kilometer'] > 0].sort_values(by='kilometer').reset_index()
    total_distance = sorted_df['kilometer'].max() - sorted_df['kilometer'].min() if not sorted_df.empty else 0
    
    avg_consumption = (total_fuel_cost / total_distance) if total_distance > 0 else 0
    
    def calculate_liters(row):
        price = fuel_prices.get(row['fuel_type'])
        return row['fuel_cost'] / price if price and price > 0 and row['fuel_cost'] > 0 else 0
    
    df['liters'] = df.apply(calculate_liters, axis=1)
    
    return {
        "total_cost": total_fuel_cost + total_service_cost, 
        "total_distance": total_distance, 
        "avg_consumption": avg_consumption, 
        "total_service_cost": total_service_cost, 
        "total_liters": df['liters'].sum()
    }

# Endpoint CRUD untuk Data Riwayat
@app.post("/api/data", tags=["Data"])
def create_data_entry(data: FuelDataCreate):
    """Membuat entri data riwayat baru."""
    last_id = db_manager.insert_data(data.dict())
    if last_id is None:
        raise HTTPException(status_code=500, detail="Gagal menyimpan data.")
    db_manager.create_backup(backup_type="auto_daily")
    with db_manager._get_connection() as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM fuel_data WHERE id = ?", (last_id,))
        return c.fetchone()

@app.put("/api/data/{id}", tags=["Data"])
def update_data_entry(id: int, data: FuelDataUpdate):
    """Memperbarui entri data riwayat yang sudah ada."""
    update_payload = data.dict(exclude_unset=True)
    if not update_payload:
        raise HTTPException(status_code=400, detail="Tidak ada data untuk diperbarui.")
    
    rowcount = db_manager.update_data(id, update_payload)
    if rowcount == 0:
        raise HTTPException(status_code=404, detail=f"Data dengan id {id} tidak ditemukan")
    
    db_manager.create_backup(backup_type="auto_daily")
    with db_manager._get_connection() as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM fuel_data WHERE id = ?", (id,))
        return c.fetchone()

@app.delete("/api/data/{id}", tags=["Data"])
def delete_data_entry(id: int):
    """Menghapus entri data riwayat."""
    if db_manager.delete_data(id) == 0:
        raise HTTPException(status_code=404, detail=f"Data dengan id {id} tidak ditemukan")
    return {"detail": "Berhasil dihapus"}

# Endpoint untuk Fungsi Admin
@app.post("/api/admin/backup", tags=["Admin"])
def trigger_backup():
    """Memicu pembuatan backup database secara manual."""
    try:
        path = db_manager.create_backup(backup_type="manual")
        return {"detail": "Backup berhasil dibuat", "path": path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/restore", tags=["Admin"])
async def restore_from_backup(file: UploadFile = File(...)):
    """Me-restore database dari file backup yang diunggah."""
    if not file.filename.endswith('.db'):
        raise HTTPException(status_code=400, detail="Tipe file tidak valid. Harap unggah file .db.")
    
    temp_path = os.path.join(db_manager.backup_dir, f"restore_{file.filename}")
    
    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        db_manager.restore_database(temp_path)
        return {"detail": f"Database berhasil di-restore dari {file.filename}."}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Terjadi error saat restore: {str(e)}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

# Endpoint untuk Ekspor Data
@app.get("/api/drivers/{driver_id}/export", tags=["Admin"])
def export_vehicle_data_to_excel(driver_id: int, start_date: Optional[str] = None, end_date: Optional[str] = None):
    """Mengekspor data satu driver ke file Excel."""
    raw_data = db_manager.fetch_data(driver_id, start_date, end_date)
    if not raw_data:
        raise HTTPException(status_code=404, detail="Tidak ada data untuk kriteria yang dipilih.")
    
    df = pd.DataFrame(raw_data)
    daily_target = 175
    df['point'] = df.apply(lambda row: (((row['granit'] * 1.5) + row['keramik']) / daily_target) if daily_target > 0 else 0, axis=1).round(2)
    df['date'] = pd.to_datetime(df['date']).dt.strftime('%d-%m-%Y')
    df = df.rename(columns={
        'date': 'Tanggal', 'plate': 'Plat Nomor', 'kilometer': 'Kilometer', 
        'fuel_cost': 'Biaya BBM', 'service_type': 'Jenis Servis', 'service_cost': 'Biaya Servis', 
        'granit': 'Granit (dus)', 'keramik': 'Keramik (dus)', 'point': 'Point'
    })
    final_df = df[['Tanggal', 'Plat Nomor', 'Kilometer', 'Biaya BBM', 'Jenis Servis', 'Biaya Servis', 'Granit (dus)', 'Keramik (dus)', 'Point']]
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        final_df.to_excel(writer, index=False, sheet_name='Data Kendaraan')
        worksheet = writer.sheets['Data Kendaraan']
        for i, col in enumerate(final_df.columns):
            column_len = max(final_df[col].astype(str).map(len).max(), len(col)) + 2
            worksheet.set_column(i, i, column_len)
            
    output.seek(0)
    
    driver_info = next((d for d in db_manager.fetch_drivers() if d['id'] == driver_id), None)
    plate = driver_info['plate'].replace(" ", "") if driver_info else str(driver_id)
    filename = f'export_{plate}_{datetime.now().strftime("%Y%m%d")}.xlsx'
    headers = {'Content-Disposition': f'attachment; filename="{filename}"'}
    
    return StreamingResponse(output, headers=headers, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.get("/api/export/all", tags=["Admin"])
def export_all_drivers_to_excel(start_date: Optional[str] = None, end_date: Optional[str] = None):
    """Mengekspor data semua driver ke satu file Excel dengan sheet terpisah."""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        all_drivers = db_manager.fetch_drivers()
        for driver in all_drivers:
            raw_data = db_manager.fetch_data(driver['id'], start_date, end_date)
            if not raw_data:
                continue
            
            df = pd.DataFrame(raw_data)
            daily_target = 175
            df['point'] = df.apply(lambda row: (((row['granit'] * 1.5) + row['keramik']) / daily_target) if daily_target > 0 else 0, axis=1).round(2)
            df['date'] = pd.to_datetime(df['date']).dt.strftime('%d-%m-%Y')
            df = df.rename(columns={
                'date': 'Tanggal', 'plate': 'Plat Nomor', 'kilometer': 'Kilometer', 
                'fuel_cost': 'Biaya BBM', 'service_type': 'Jenis Servis', 'service_cost': 'Biaya Servis', 
                'granit': 'Granit (dus)', 'keramik': 'Keramik (dus)', 'point': 'Point'
            })
            final_df = df[['Tanggal', 'Plat Nomor', 'Kilometer', 'Biaya BBM', 'Jenis Servis', 'Biaya Servis', 'Granit (dus)', 'Keramik (dus)', 'Point']]
            
            sheet_name = ''.join(e for e in driver['name'] if e.isalnum())[:31]
            final_df.to_excel(writer, index=False, sheet_name=sheet_name)
            
            worksheet = writer.sheets[sheet_name]
            for i, col in enumerate(final_df.columns):
                column_len = max(final_df[col].astype(str).map(len).max(), len(col)) + 2
                worksheet.set_column(i, i, column_len)

    output.seek(0)
    filename = f'export_semua_driver_{datetime.now().strftime("%Y%m%d")}.xlsx'
    headers = {'Content-Disposition': f'attachment; filename="{filename}"'}
    
    return StreamingResponse(output, headers=headers, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
