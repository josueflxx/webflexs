from django.db import migrations


def _ensure_company(Company, *, name, slug, legal_name="", email=""):
    company, _ = Company.objects.get_or_create(
        slug=slug,
        defaults={
            "name": name,
            "legal_name": legal_name or name,
            "email": email,
            "is_active": True,
        },
    )
    updates = []
    if company.name != name:
        company.name = name
        updates.append("name")
    if not company.legal_name:
        company.legal_name = legal_name or name
        updates.append("legal_name")
    if not company.slug:
        company.slug = slug
        updates.append("slug")
    if email and not company.email:
        company.email = email
        updates.append("email")
    if updates:
        company.save(update_fields=updates)
    return company


def seed_companies_and_backfill(apps, schema_editor):
    Company = apps.get_model("core", "Company")
    ClientProfile = apps.get_model("accounts", "ClientProfile")
    ClientCompany = apps.get_model("accounts", "ClientCompany")
    Order = apps.get_model("orders", "Order")
    Cart = apps.get_model("orders", "Cart")
    ClampQuotation = apps.get_model("orders", "ClampQuotation")
    ClampMeasureRequest = apps.get_model("catalog", "ClampMeasureRequest")
    ClientPayment = apps.get_model("accounts", "ClientPayment")
    ClientTransaction = apps.get_model("accounts", "ClientTransaction")
    ImportExecution = apps.get_model("core", "ImportExecution")

    flexs = _ensure_company(
        Company,
        name="Flexs",
        slug="flexs",
        legal_name="Flexs",
        email="ventas@flexs.com.ar",
    )
    _ensure_company(
        Company,
        name="Ubolt",
        slug="ubolt",
        legal_name="Ubolt",
    )

    default_company = flexs

    # Create client-company links for existing clients.
    for profile in ClientProfile.objects.all().iterator():
        ClientCompany.objects.get_or_create(
            client_profile=profile,
            company=default_company,
            defaults={
                "client_category": profile.client_category,
                "discount_percentage": profile.discount,
                "is_active": bool(profile.is_approved),
            },
        )

    # Assign default company to existing operational records.
    Order.objects.filter(company__isnull=True).update(company=default_company)
    Cart.objects.filter(company__isnull=True).update(company=default_company)
    ClampQuotation.objects.filter(company__isnull=True).update(company=default_company)
    ClampMeasureRequest.objects.filter(company__isnull=True).update(company=default_company)
    ImportExecution.objects.filter(company__isnull=True).update(company=default_company)

    # Backfill payments with company.
    for payment in ClientPayment.objects.filter(company__isnull=True).select_related("order").iterator():
        if payment.order_id and getattr(payment.order, "company_id", None):
            payment.company = payment.order.company
        else:
            payment.company = default_company
        payment.save(update_fields=["company"])

    # Backfill ledger transactions with company.
    for tx in (
        ClientTransaction.objects.filter(company__isnull=True)
        .select_related("order", "payment")
        .iterator()
    ):
        if tx.order_id and getattr(tx.order, "company_id", None):
            tx.company = tx.order.company
        elif tx.payment_id and getattr(tx.payment, "company_id", None):
            tx.company = tx.payment.company
        else:
            tx.company = default_company
        tx.save(update_fields=["company"])

    # Attach client_company_ref to orders when possible.
    profile_map = dict(ClientProfile.objects.values_list("user_id", "id"))
    company_links = {
        (link.client_profile_id, link.company_id): link.id
        for link in ClientCompany.objects.filter(company=default_company).only("id", "client_profile_id", "company_id")
    }
    orders_to_update = []
    for order in (
        Order.objects.filter(company=default_company, client_company_ref__isnull=True)
        .only("id", "user_id")
        .iterator()
    ):
        if not order.user_id:
            continue
        profile_id = profile_map.get(order.user_id)
        if not profile_id:
            continue
        link_id = company_links.get((profile_id, default_company.id))
        if not link_id:
            continue
        order.client_company_ref_id = link_id
        orders_to_update.append(order)

    if orders_to_update:
        Order.objects.bulk_update(orders_to_update, ["client_company_ref"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_company_importexecution_company"),
        ("accounts", "0009_clientcompany_clientpayment_company_and_more"),
        ("orders", "0008_cart_company_clampquotation_company_and_more"),
        ("catalog", "0015_clampmeasurerequest_company"),
    ]

    operations = [
        migrations.RunPython(seed_companies_and_backfill, noop_reverse),
    ]
