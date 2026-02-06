
import uuid
from django.core.cache import cache

class ImportTaskManager:
    """
    Manages state for background import tasks using Django Cache.
    Keys are stored as 'import_task_{task_id}'.
    """
    
    CACHE_TIMEOUT = 3600 # 1 hour
    
    @staticmethod
    def start_task():
        """Creates a new task ID and initializes state."""
        task_id = str(uuid.uuid4())
        state = {
            'status': 'starting',
            'current': 0,
            'total': 0,
            'message': 'Iniciando...',
            'result': None
        }
        cache.set(f'import_task_{task_id}', state, ImportTaskManager.CACHE_TIMEOUT)
        return task_id

    @staticmethod
    def update_progress(task_id, current, total, message=None):
        """Updates progress of a task."""
        key = f'import_task_{task_id}'
        state = cache.get(key) or {}
        state.update({
            'status': 'processing',
            'current': current,
            'total': total
        })
        if message:
            state['message'] = message
        
        cache.set(key, state, ImportTaskManager.CACHE_TIMEOUT)

    @staticmethod
    def complete_task(task_id, result_data):
        """Marks task as complete and stores result summary."""
        key = f'import_task_{task_id}'
        state = cache.get(key) or {}
        state.update({
            'status': 'completed',
            'current': state.get('total', 0),
            'message': 'Completado',
            'result': result_data # Serializable summary
        })
        cache.set(key, state, ImportTaskManager.CACHE_TIMEOUT)
        
    @staticmethod
    def fail_task(task_id, error_message):
        """Marks task as failed."""
        key = f'import_task_{task_id}'
        state = cache.get(key) or {}
        state.update({
            'status': 'failed',
            'message': error_message
        })
        cache.set(key, state, ImportTaskManager.CACHE_TIMEOUT)

    @staticmethod
    def get_status(task_id):
        """Retrieves task status."""
        return cache.get(f'import_task_{task_id}')
