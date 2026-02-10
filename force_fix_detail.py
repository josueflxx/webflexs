
import os

file_path = r'c:\Users\Brian\Desktop\webflexs\admin_panel\templates\admin_panel\orders\detail.html'

content = """{% extends 'admin_panel/base.html' %}

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
                <strong>Descuento ({{ order.discount_percentage|floatformat:0 }}%):</strong> -${{ order.discount_amount|floatformat:2 }}<br>
                {% endif %}
                <strong>Total:</strong> <span style="font-size: 1.25rem; color: var(--color-primary);">${{ order.total|floatformat:2 }}</span>
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

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print(f"File overwritten: {file_path}")

# Verify immediately
with open(file_path, 'r', encoding='utf-8') as f:
    read_content = f.read()
    
if "{% if order.status == value %}" in read_content:
    print("VERIFICATION SUCCESS: Spaces found around ==")
else:
    print("VERIFICATION FAILED: Spaces NOT found around ==")

if "${{ order.total|floatformat:2 }}" in read_content:
     print("VERIFICATION SUCCESS: Total tag is single line")
else:
     print("VERIFICATION FAILED: Total tag is split")
