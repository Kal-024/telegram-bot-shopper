import os
import logging
from pymongo import MongoClient

_client = None
_db = None

def get_db():
    """
    Retorna la instancia de la base de datos MongoDB si está configurada.
    De lo contrario retorna None (fallback a JSON).
    """
    global _client, _db
    if _db is not None:
        return _db
    
    mongo_url = os.environ.get('MONGO_URL')
    if mongo_url:
        try:
            _client = MongoClient(mongo_url)
            db_name = os.environ.get('MONGO_DB', 'telegram_shopper')
            _db = _client[db_name]
            logging.info(f"✅ Conectado a MongoDB: Base de datos '{db_name}'")
            return _db
        except Exception as e:
            logging.error(f"❌ Error conectando a MongoDB: {e}")
            return None
    return None
