from fastapi import APIRouter, Request, Body
from app.engine import evaluate
from app.web import render
from app import store, publish
router = APIRouter()


@router.get('/calibration')
def page(request: Request):
    return render(request, 'calibration')


@router.post('/api/calibration')
def calculate(payload: dict = Body(default={})):
    return evaluate(payload, until=6)


@router.post('/api/runs/{run_id}/calibration/publish')
def publish_adjustment(run_id: str):
    from app.main import DB_PATH
    result = store.get(DB_PATH, run_id)
    return publish.publish(DB_PATH, result)


@router.post('/api/runs/{run_id}/calibration/restore/{pub_id}')
def restore_adjustment(run_id: str, pub_id: str):
    from app.main import DB_PATH
    from app.errors import BusinessError
    result = store.get(DB_PATH, run_id)
    publication = publish.get(DB_PATH, pub_id)
    if publication['source_key'] != publish.source_key(result):
        raise BusinessError('NOT_FOUND', '发布记录不属于该批数据来源', 404)
    return publish.restore(DB_PATH, pub_id)


@router.get('/api/runs/{run_id}/calibrations')
def publications(run_id: str):
    from app.main import DB_PATH
    result = store.get(DB_PATH, run_id)
    if not result.get('input_snapshot'):
        return {'active': None, 'history': []}
    return {'active': publish.active(DB_PATH, result), 'history': publish.history(DB_PATH, result)}
