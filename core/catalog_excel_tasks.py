"""Only consumed by the dedicated flexs-catalog-excel queue worker."""
from celery import shared_task


@shared_task(name="core.generate_client_catalog_excel", ignore_result=True,
             soft_time_limit=540, time_limit=600)
def generate_client_catalog_excel(spec, token):
    from core.services.catalog_excel_jobs import generate_export

    generate_export(spec, token)
