
import os

# 1. requests/list.html
requests_list_content = r"""{% extends 'admin_panel/base.html' %}

{% block page_title %}Solicitudes de Cuenta{% endblock %}

{% block content %}
<div class="admin-table-container">
    <div class="admin-toolbar">
        <form method="get" class="toolbar-search">
            <select name="status" class="form-select" onchange="this.form.submit()">
                <option value="pending" {% if status_filter == 'pending' %}selected{% endif %}>Pendientes</option>
                <option value="approved" {% if status_filter == 'approved' %}selected{% endif %}>Aprobadas</option>
                <option value="rejected" {% if status_filter == 'rejected' %}selected{% endif %}>Rechazadas</option>
                <option value="" {% if not status_filter %}selected{% endif %}>Todas</option>
            </select>
        </form>
    </div>

    <table class="admin-table">
        <thead>
            <tr>
                <th>Fecha</th>
                <th>Empresa</th>
                <th>Contacto</th>
                <th>Email</th>
                <th>Teléfono</th>
                <th>Estado</th>
                <th>Acciones</th>
            </tr>
        </thead>
        <tbody>
            {% for req in page_obj %}
            <tr>
                <td>{{ req.created_at|date:"d/m/Y" }}</td>
                <td><strong>{{ req.company_name }}</strong></td>
                <td>{{ req.contact_name }}</td>
                <td>{{ req.email }}</td>
                <td>{{ req.phone }}</td>
                <td>
                    <span
                        class="badge badge-{% if req.status == 'pending' %}warning{% elif req.status == 'approved' %}success{% else %}danger{% endif %}">
                        {{ req.get_status_display }}
                    </span>
                </td>
                <td class="actions">
                    {% if req.status == 'pending' %}
                    <a href="{% url 'admin_request_approve' req.pk %}" class="btn btn-primary">Aprobar</a>
                    <form method="post" action="{% url 'admin_request_reject' req.pk %}" style="display: inline;">
                        {% csrf_token %}
                        <button type="submit" class="btn btn-outline"
                            onclick="return confirm('¿Rechazar esta solicitud?')">Rechazar</button>
                    </form>
                    {% else %}
                    -
                    {% endif %}
                </td>
            </tr>
            {% empty %}
            <tr>
                <td colspan="7" class="text-center text-muted">No hay solicitudes</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
</div>
{% endblock %}
"""

# 2. orders/list.html
orders_list_content = r"""{% extends 'admin_panel/base.html' %}

{% block page_title %}Pedidos{% endblock %}

{% block content %}
<div class="admin-table-container">
    <div class="admin-toolbar">
        <form method="get" class="toolbar-search">
            <input type="text" name="client" value="{{ client }}" placeholder="Buscar cliente..." class="form-input">
            <select name="status" class="form-select">
                <option value="">Todos los estados</option>
                {% for value, label in status_choices %}
                <option value="{{ value }}" {% if status == value %}selected{% endif %}>{{ label }}</option>
                {% endfor %}
            </select>
            <button type="submit" class="btn btn-primary">Filtrar</button>
        </form>
    </div>

    <table class="admin-table">
        <thead>
            <tr>
                <th>#</th>
                <th>Fecha</th>
                <th>Cliente</th>
                <th>Items</th>
                <th>Total</th>
                <th>Estado</th>
                <th>Acciones</th>
            </tr>
        </thead>
        <tbody>
            {% for order in page_obj %}
            <tr>
                <td><strong>{{ order.pk }}</strong></td>
                <td>{{ order.created_at|date:"d/m/Y H:i" }}</td>
                <td>{{ order.user.username|default:"N/A" }}</td>
                <td>{{ order.get_item_count }}</td>
                <td>${{ order.total|floatformat:2 }}</td>
                <td>
                    <span
                        class="badge badge-{% if order.status == 'pending' %}warning{% elif order.status == 'confirmed' %}info{% elif order.status == 'cancelled' %}danger{% else %}success{% endif %}">
                        {{ order.get_status_display }}
                    </span>
                </td>
                <td class="actions">
                    <a href="{% url 'admin_order_detail' order.pk %}" class="btn btn-outline">Ver</a>
                </td>
            </tr>
            {% empty %}
            <tr>
                <td colspan="7" class="text-center text-muted">No hay pedidos</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>

    {% if page_obj.paginator.num_pages > 1 %}
    <div class="admin-toolbar">
        <span>Página {{ page_obj.number }} de {{ page_obj.paginator.num_pages }}</span>
        <div class="toolbar-actions">
            {% if page_obj.has_previous %}
            <a href="?page={{ page_obj.previous_page_number }}" class="btn btn-outline">← Anterior</a>
            {% endif %}
            {% if page_obj.has_next %}
            <a href="?page={{ page_obj.next_page_number }}" class="btn btn-outline">Siguiente →</a>
            {% endif %}
        </div>
    </div>
    {% endif %}
</div>
{% endblock %}
"""

# 3. orders/detail.html
orders_detail_content = r"""{% extends 'admin_panel/base.html' %}

{% block page_title %}Pedido #{{ order.pk }}{% endblock %}

{% block content %}
<div class="admin-form-container" style="max-width: 900px;">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.5rem;">
        <h2 style="margin: 0;">Pedido #{{ order.pk }}</h2>
        <span
            class="badge badge-{% if order.status == 'pending' %}warning{% elif order.status == 'confirmed' %}info{% elif order.status == 'cancelled' %}danger{% else %}success{% endif %}"
            style="font-size: 1rem; padding: 0.5rem 1rem;">
            {{ order.get_status_display }}
        </span>
    </div>

    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; margin-bottom: 2rem;">
        <div>
            <h4>Cliente</h4>
            <p>
                <strong>{{ order.client_company|default:order.user.username }}</strong><br>
                {% if order.client_cuit %}CUIT: {{ order.client_cuit }}<br>{% endif %}
                {{ order.client_address }}<br>
                {{ order.client_phone }}
            </p>
        </div>
        <div>
            <h4>Información</h4>
            <p>
                <strong>Fecha:</strong> {{ order.created_at|date:"d/m/Y H:i" }}<br>
                <strong>Subtotal:</strong> ${{ order.subtotal|floatformat:2 }}<br>
                {% if order.discount_percentage %}
                <strong>Descuento ({{ order.discount_percentage|floatformat:0 }}%):</strong> -${{
                order.discount_amount|floatformat:2 }}<br>
                {% endif %}
                <strong>Total:</strong> <span style="font-size: 1.25rem; color: var(--color-primary);">${{
                    order.total|floatformat:2 }}</span>
            </p>
        </div>
    </div>

    {% if order.notes %}
    <div style="background: var(--color-dark); padding: 1rem; border-radius: 8px; margin-bottom: 2rem;">
        <strong>Notas del cliente:</strong>
        <p>{{ order.notes }}</p>
    </div>
    {% endif %}

    <h4>Items del Pedido</h4>
    <table class="admin-table" style="margin-bottom: 2rem;">
        <thead>
            <tr>
                <th>SKU</th>
                <th>Producto</th>
                <th>Cant.</th>
                <th>Precio Unit.</th>
                <th>Subtotal</th>
            </tr>
        </thead>
        <tbody>
            {% for item in order.items.all %}
            <tr>
                <td><code>{{ item.product_sku }}</code></td>
                <td>{{ item.product_name }}</td>
                <td>{{ item.quantity }}</td>
                <td>${{ item.price_at_purchase|floatformat:2 }}</td>
                <td>${{ item.subtotal|floatformat:2 }}</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>

    <h4>Cambiar Estado</h4>
    <form method="post" style="display: flex; gap: 1rem; align-items: flex-end;">
        {% csrf_token %}
        <div class="form-group" style="margin: 0; flex: 1;">
            <select name="status" class="form-select">
                {% for value, label in status_choices %}
                <option value="{{ value }}" {% if order.status == value %}selected{% endif %}>{{ label }}</option>
                {% endfor %}
            </select>
        </div>
        <button type="submit" class="btn btn-primary">Actualizar Estado</button>
    </form>

    <div class="form-actions">
        <a href="{% url 'admin_order_list' %}" class="btn btn-outline">← Volver a Pedidos</a>
    </div>
</div>
{% endblock %}
"""

# 4. products/form.html
products_form_content = r"""{% extends 'admin_panel/base.html' %}

{% block page_title %}{{ action }} Producto{% endblock %}

{% block content %}
<div class="admin-form-container">
    <h2>{{ action }} Producto</h2>

    <form method="post">
        {% csrf_token %}

        <div class="form-group">
            <label for="sku" class="form-label">SKU *</label>
            <input type="text" name="sku" id="sku" class="form-input" required value="{{ product.sku|default:'' }}">
        </div>

        <div class="form-group">
            <label for="name" class="form-label">Nombre *</label>
            <input type="text" name="name" id="name" class="form-input" required value="{{ product.name|default:'' }}">
        </div>

        <div class="form-group">
            <label for="price" class="form-label">Precio *</label>
            <input type="number" step="0.01" name="price" id="price" class="form-input" required
                value="{{ product.price|default:'0' }}">
        </div>

        <div class="form-group">
            <label for="stock" class="form-label">Stock</label>
            <input type="number" name="stock" id="stock" class="form-input" value="{{ product.stock|default:'0' }}">
        </div>

        <div class="form-group">
            <label for="category" class="form-label">Categoría</label>
            <select name="category" id="category" class="form-select">
                <option value="">Sin categoría</option>
                {% for cat in categories %}
                <option value="{{ cat.pk }}" {% if product.category_id == cat.pk %}selected{% endif %}>{{ cat.name }}
                </option>
                {% endfor %}
            </select>
        </div>

        <div class="form-group">
            <label for="description" class="form-label">Descripción</label>
            <textarea name="description" id="description"
                class="form-textarea">{{ product.description|default:'' }}</textarea>
        </div>

        {% if product %}
        <div class="form-group">
            <label class="form-label">
                <input type="checkbox" name="is_active" {% if product.is_active %}checked{% endif %}>
                Producto activo
            </label>
        </div>
        {% endif %}

        <div class="form-actions">
            <button type="submit" class="btn btn-primary">Guardar</button>
            <a href="{% url 'admin_product_list' %}" class="btn btn-outline">Cancelar</a>
        </div>
    </form>
</div>
{% endblock %}
"""

# 5. clients/form.html
clients_form_content = r"""{% extends 'admin_panel/base.html' %}

{% block page_title %}Editar Cliente{% endblock %}

{% block content %}
<div class="admin-form-container">
    <h2>Editar Cliente: {{ client.company_name }}</h2>

    <form method="post">
        {% csrf_token %}

        <div class="form-group">
            <label class="form-label">Usuario</label>
            <input type="text" class="form-input" value="{{ client.user.username }}" disabled>
        </div>

        <div class="form-group">
            <label for="company_name" class="form-label">Empresa / Razón Social</label>
            <input type="text" name="company_name" id="company_name" class="form-input"
                value="{{ client.company_name }}">
        </div>

        <div class="form-group">
            <label for="cuit_dni" class="form-label">CUIT / DNI</label>
            <input type="text" name="cuit_dni" id="cuit_dni" class="form-input" value="{{ client.cuit_dni }}">
        </div>

        <div class="form-group">
            <label for="discount" class="form-label">Descuento (%)</label>
            <input type="number" step="0.01" name="discount" id="discount" class="form-input"
                value="{{ client.discount }}">
            <small class="text-muted">Ejemplo: 10 para 10% de descuento</small>
        </div>

        <div class="form-group">
            <label for="province" class="form-label">Provincia</label>
            <input type="text" name="province" id="province" class="form-input" value="{{ client.province }}">
        </div>

        <div class="form-group">
            <label for="address" class="form-label">Domicilio</label>
            <textarea name="address" id="address" class="form-textarea">{{ client.address }}</textarea>
        </div>

        <div class="form-group">
            <label for="phone" class="form-label">Teléfonos</label>
            <input type="text" name="phone" id="phone" class="form-input" value="{{ client.phone }}">
        </div>

        <div class="form-group">
            <label for="client_type" class="form-label">Tipo de Cliente</label>
            <select name="client_type" id="client_type" class="form-select">
                <option value="">-</option>
                <option value="taller" {% if client.client_type == 'taller' %}selected{% endif %}>Taller</option>
                <option value="distribuidora" {% if client.client_type == 'distribuidora' %}selected{% endif %}>
                    Distribuidora</option>
                <option value="flota" {% if client.client_type == 'flota' %}selected{% endif %}>Flota</option>
                <option value="otro" {% if client.client_type == 'otro' %}selected{% endif %}>Otro</option>
            </select>
        </div>

        <div class="form-group">
            <label for="iva_condition" class="form-label">Condición IVA</label>
            <select name="iva_condition" id="iva_condition" class="form-select">
                <option value="">-</option>
                <option value="responsable_inscripto" {% if client.iva_condition == 'responsable_inscripto' %}selected{%
                    endif %}>Responsable Inscripto</option>
                <option value="monotributista" {% if client.iva_condition == 'monotributista' %}selected{% endif %}>
                    Monotributista</option>
                <option value="exento" {% if client.iva_condition == 'exento' %}selected{% endif %}>Exento</option>
                <option value="consumidor_final" {% if client.iva_condition == 'consumidor_final' %}selected{% endif %}>
                    Consumidor Final</option>
            </select>
        </div>

        <div class="form-actions">
            <button type="submit" class="btn btn-primary">Guardar</button>
            <a href="{% url 'admin_client_list' %}" class="btn btn-outline">Cancelar</a>
        </div>
    </form>
</div>
{% endblock %}
"""

# 6. categories/form.html
categories_form_content = r"""{% extends 'admin_panel/base.html' %}

{% block page_title %}{{ action }} Categoría{% endblock %}

{% block content %}
<div class="admin-form-container">
    <h2>{{ action }} Categoría</h2>

    <form method="post">
        {% csrf_token %}

        <div class="form-group">
            <label for="name" class="form-label">Nombre *</label>
            <input type="text" name="name" id="name" class="form-input" required value="{{ category.name|default:'' }}">
        </div>

        <div class="form-group">
            <label for="parent" class="form-label">Categoría Padre (opcional)</label>
            <select name="parent" id="parent" class="form-select">
                <option value="">-- Sin padre (categoría principal) --</option>
                {% for cat in parent_categories %}
                <option value="{{ cat.pk }}" {% if category.parent_id == cat.pk %}selected{% endif %}>{{ cat.name }}
                </option>
                {% endfor %}
            </select>
        </div>

        {% if category %}
        <div class="form-group">
            <label class="form-label">
                <input type="checkbox" name="is_active" {% if category.is_active %}checked{% endif %}>
                Categoría activa
            </label>
        </div>
        {% endif %}

        <div class="form-actions">
            <button type="submit" class="btn btn-primary">Guardar</button>
            <a href="{% url 'admin_category_list' %}" class="btn btn-outline">Cancelar</a>
        </div>
    </form>
</div>
{% endblock %}
"""

files_to_fix = {
    r'c:\Users\Brian\Desktop\webflexs\admin_panel\templates\admin_panel\requests\list.html': requests_list_content,
    r'c:\Users\Brian\Desktop\webflexs\admin_panel\templates\admin_panel\orders\list.html': orders_list_content,
    r'c:\Users\Brian\Desktop\webflexs\admin_panel\templates\admin_panel\orders\detail.html': orders_detail_content,
    r'c:\Users\Brian\Desktop\webflexs\admin_panel\templates\admin_panel\products\form.html': products_form_content,
    r'c:\Users\Brian\Desktop\webflexs\admin_panel\templates\admin_panel\clients\form.html': clients_form_content,
    r'c:\Users\Brian\Desktop\webflexs\admin_panel\templates\admin_panel\categories\form.html': categories_form_content,
}

for path, content in files_to_fix.items():
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Successfully wrote {path} with corrected syntax")
    except Exception as e:
        print(f"Error writing {path}: {e}")
