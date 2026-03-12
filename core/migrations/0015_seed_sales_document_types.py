from django.db import migrations
from django.db.models import Max


def _max_internal_number(InternalDocument, *, company, doc_type, using):
    max_number = (
        InternalDocument.objects.using(using)
        .filter(company=company, doc_type=doc_type)
        .aggregate(value=Max("number"))
        .get("value")
    )
    return int(max_number or 0)


def _max_fiscal_number(FiscalDocument, *, company, point_of_sale, doc_type, using):
    max_number = (
        FiscalDocument.objects.using(using)
        .filter(company=company, point_of_sale=point_of_sale, doc_type=doc_type)
        .aggregate(value=Max("number"))
        .get("value")
    )
    return int(max_number or 0)


def seed_sales_document_types(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    Company = apps.get_model("core", "Company")
    Warehouse = apps.get_model("core", "Warehouse")
    SalesDocumentType = apps.get_model("core", "SalesDocumentType")
    FiscalPointOfSale = apps.get_model("core", "FiscalPointOfSale")
    DocumentSeries = apps.get_model("core", "DocumentSeries")
    FiscalDocumentSeries = apps.get_model("core", "FiscalDocumentSeries")
    InternalDocument = apps.get_model("core", "InternalDocument")
    FiscalDocument = apps.get_model("core", "FiscalDocument")

    internal_series_map = {
        (row["company_id"], row["doc_type"]): max(int(row["next_number"] or 1) - 1, 0)
        for row in DocumentSeries.objects.using(db_alias).values("company_id", "doc_type", "next_number")
    }
    fiscal_series_map = {
        (row["company_id"], row["point_of_sale_ref_id"], row["doc_type"]): max(int(row["next_number"] or 1) - 1, 0)
        for row in FiscalDocumentSeries.objects.using(db_alias).values(
            "company_id",
            "point_of_sale_ref_id",
            "doc_type",
            "next_number",
        )
    }

    for company in Company.objects.using(db_alias).all():
        warehouse, _ = Warehouse.objects.using(db_alias).get_or_create(
            company=company,
            code="principal",
            defaults={
                "name": "Deposito principal",
                "is_active": True,
                "notes": "Generado automaticamente para tipos de documento configurables.",
            },
        )

        if SalesDocumentType.objects.using(db_alias).filter(company=company).exists():
            continue

        default_point = (
            FiscalPointOfSale.objects.using(db_alias)
            .filter(company=company, is_active=True)
            .order_by("-is_default", "number")
            .first()
        )
        prefer_a = (company.tax_condition or "").strip() == "responsable_inscripto"

        def internal_last_number(doc_type):
            return max(
                internal_series_map.get((company.id, doc_type), 0),
                _max_internal_number(
                    InternalDocument,
                    company=company,
                    doc_type=doc_type,
                    using=db_alias,
                ),
            )

        def fiscal_last_number(doc_type):
            if not default_point:
                return 0
            return max(
                fiscal_series_map.get((company.id, default_point.id, doc_type), 0),
                _max_fiscal_number(
                    FiscalDocument,
                    company=company,
                    point_of_sale=default_point,
                    doc_type=doc_type,
                    using=db_alias,
                ),
            )

        seed_rows = [
            {
                "code": "cotizacion",
                "name": "Cotizacion",
                "letter": "COT",
                "document_behavior": "Cotizacion",
                "billing_mode": "INTERNAL_DOCUMENT",
                "internal_doc_type": "COT",
                "last_number": internal_last_number("COT"),
                "display_order": 10,
                "enabled": True,
                "is_default": True,
                "generate_stock_movement": False,
                "generate_account_movement": False,
                "group_equal_products": True,
            },
            {
                "code": "presupuesto",
                "name": "Presupuesto",
                "letter": "PRE",
                "document_behavior": "Presupuesto",
                "billing_mode": "INTERNAL_DOCUMENT",
                "internal_doc_type": "",
                "last_number": 0,
                "display_order": 15,
                "enabled": True,
                "is_default": True,
                "generate_stock_movement": False,
                "generate_account_movement": False,
                "group_equal_products": True,
            },
            {
                "code": "pedido",
                "name": "Pedido",
                "letter": "PED",
                "document_behavior": "Pedido",
                "billing_mode": "INTERNAL_DOCUMENT",
                "internal_doc_type": "PED",
                "last_number": internal_last_number("PED"),
                "display_order": 20,
                "enabled": True,
                "is_default": True,
                "generate_stock_movement": False,
                "generate_account_movement": False,
                "group_equal_products": True,
            },
            {
                "code": "remito",
                "name": "Remito",
                "letter": "REM",
                "document_behavior": "Remito",
                "billing_mode": "INTERNAL_DOCUMENT",
                "internal_doc_type": "REM",
                "last_number": internal_last_number("REM"),
                "display_order": 30,
                "enabled": True,
                "is_default": True,
                "generate_stock_movement": True,
                "generate_account_movement": False,
                "group_equal_products": True,
            },
            {
                "code": "recibo",
                "name": "Recibo",
                "letter": "REC",
                "document_behavior": "Recibo",
                "billing_mode": "INTERNAL_DOCUMENT",
                "internal_doc_type": "REC",
                "last_number": internal_last_number("REC"),
                "display_order": 40,
                "enabled": True,
                "is_default": True,
                "generate_stock_movement": False,
                "generate_account_movement": False,
                "group_equal_products": True,
            },
            {
                "code": "nota-debito-ajuste",
                "name": "Nota de debito / ajuste interno",
                "letter": "ND",
                "document_behavior": "NotaDebito",
                "billing_mode": "INTERNAL_DOCUMENT",
                "internal_doc_type": "AJU",
                "last_number": internal_last_number("AJU"),
                "display_order": 50,
                "enabled": True,
                "is_default": True,
                "generate_stock_movement": False,
                "generate_account_movement": False,
                "group_equal_products": True,
            },
        ]

        if default_point:
            factura_a_default = prefer_a
            nota_credito_a_default = prefer_a
            seed_rows.extend(
                [
                    {
                        "code": "factura-a",
                        "name": "Factura A",
                        "letter": "A",
                        "point_of_sale": default_point,
                        "document_behavior": "Factura",
                        "billing_mode": "ELECTRONIC_AFIP_WSFE",
                        "fiscal_doc_type": "FA",
                        "last_number": fiscal_last_number("FA"),
                        "display_order": 60,
                        "enabled": True,
                        "is_default": factura_a_default,
                        "generate_stock_movement": False,
                        "generate_account_movement": False,
                        "group_equal_products": True,
                    },
                    {
                        "code": "factura-b",
                        "name": "Factura B",
                        "letter": "B",
                        "point_of_sale": default_point,
                        "document_behavior": "Factura",
                        "billing_mode": "ELECTRONIC_AFIP_WSFE",
                        "fiscal_doc_type": "FB",
                        "last_number": fiscal_last_number("FB"),
                        "display_order": 61,
                        "enabled": True,
                        "is_default": not factura_a_default,
                        "generate_stock_movement": False,
                        "generate_account_movement": False,
                        "group_equal_products": True,
                    },
                    {
                        "code": "nota-credito-a",
                        "name": "Nota de credito A",
                        "letter": "A",
                        "point_of_sale": default_point,
                        "document_behavior": "NotaCredito",
                        "billing_mode": "ELECTRONIC_AFIP_WSFE",
                        "fiscal_doc_type": "NCA",
                        "last_number": fiscal_last_number("NCA"),
                        "display_order": 70,
                        "enabled": True,
                        "is_default": nota_credito_a_default,
                        "generate_stock_movement": False,
                        "generate_account_movement": True,
                        "group_equal_products": True,
                    },
                    {
                        "code": "nota-credito-b",
                        "name": "Nota de credito B",
                        "letter": "B",
                        "point_of_sale": default_point,
                        "document_behavior": "NotaCredito",
                        "billing_mode": "ELECTRONIC_AFIP_WSFE",
                        "fiscal_doc_type": "NCB",
                        "last_number": fiscal_last_number("NCB"),
                        "display_order": 71,
                        "enabled": True,
                        "is_default": not nota_credito_a_default,
                        "generate_stock_movement": False,
                        "generate_account_movement": True,
                        "group_equal_products": True,
                    },
                ]
            )

        for row in seed_rows:
            SalesDocumentType.objects.using(db_alias).create(
                company=company,
                default_warehouse=warehouse if row["document_behavior"] in {"Remito", "Pedido", "Factura", "NotaCredito"} else None,
                prioritize_default_warehouse=True,
                default_sales_user=None,
                point_of_sale=row.get("point_of_sale"),
                fiscal_doc_type=row.get("fiscal_doc_type", ""),
                **{key: value for key, value in row.items() if key not in {"point_of_sale", "fiscal_doc_type"}},
            )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0014_salesdocumenttype_fiscaldocument_sales_document_type_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_sales_document_types, noop_reverse),
    ]
