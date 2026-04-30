from django.core.management.base import BaseCommand, CommandError

from core.models import Company
from core.services.demo_sales_flow import ensure_demo_sales_flow


class Command(BaseCommand):
    help = "Crea o actualiza un caso demo local completo para validar solicitud -> pedido -> remito -> factura -> cobro."

    def add_arguments(self, parser):
        parser.add_argument(
            "--company-id",
            type=int,
            default=None,
            help="ID de empresa donde sembrar el demo. Si se omite, usa la primera activa.",
        )
        parser.add_argument(
            "--actor",
            type=str,
            default="",
            help="Username staff a usar como actor de auditoria para el demo.",
        )

    def handle(self, *args, **options):
        company = self._resolve_company(company_id=options.get("company_id"))
        actor = self._resolve_actor(username=options.get("actor", ""))

        result = ensure_demo_sales_flow(company=company, actor=actor)

        self.stdout.write(self.style.SUCCESS("Demo comercial listo."))
        self.stdout.write(f"Empresa: {result.company.name} (id={result.company.pk})")
        self.stdout.write(f"Cliente demo: {result.client_profile.company_name} (id={result.client_profile.pk})")
        self.stdout.write(f"Solicitud demo: #{result.order_request.pk} [{result.order_request.status}]")
        self.stdout.write(f"Pedido demo: #{result.order.pk} [{result.order.status}]")
        self.stdout.write(
            f"Remito: {result.remito.display_number if result.remito else '-'}"
        )
        self.stdout.write(
            f"Factura: {result.invoice.display_number if result.invoice else '-'}"
        )
        self.stdout.write(
            f"Cobro: #{result.payment.pk if result.payment else '-'} ref={result.payment.reference if result.payment else '-'}"
        )
        self.stdout.write(
            f"Cuenta corriente demo: ${result.client_profile.get_current_balance(company=result.company):.2f}"
        )
        self.stdout.write("Confirmacion manual sugerida:")
        self.stdout.write(f"  1. Abrir la solicitud web: /admin-panel/solicitudes/{result.order_request.pk}/")
        self.stdout.write(f"  2. Abrir la ficha de venta: /admin-panel/pedidos/{result.order.pk}/")
        self.stdout.write(f"  3. Abrir la ficha del cliente: /admin-panel/clientes/{result.client_profile.pk}/historial/")
        if result.invoice:
            self.stdout.write(f"  4. Abrir la factura: /admin-panel/fiscal/documentos/{result.invoice.pk}/")
        if result.payment:
            self.stdout.write("  5. Revisar el cobro en /admin-panel/pagos/")
        self.stdout.write(
            "URLs sugeridas:"
            f" /admin-panel/pedidos/{result.order.pk}/"
            f" /admin-panel/solicitudes/{result.order_request.pk}/"
            f" /admin-panel/clientes/{result.client_profile.pk}/historial/"
        )

    def _resolve_company(self, *, company_id=None):
        queryset = Company.objects.filter(is_active=True).order_by("id")
        if company_id:
            company = queryset.filter(pk=company_id).first()
            if not company:
                raise CommandError(f"No existe empresa activa con id={company_id}.")
            return company
        company = queryset.first()
        if not company:
            raise CommandError("No hay empresas activas para sembrar el demo.")
        return company

    def _resolve_actor(self, *, username=""):
        username = str(username or "").strip()
        if not username:
            return None
        from django.contrib.auth.models import User

        actor = User.objects.filter(username=username, is_staff=True, is_active=True).first()
        if not actor:
            raise CommandError(f"No existe staff activo con username={username}.")
        return actor
