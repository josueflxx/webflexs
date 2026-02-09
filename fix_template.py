
import os

content = """{% extends 'admin_panel/base.html' %}

{% block page_title %}{{ action }} Categoría{% endblock %}

{% block content %}
<div class="admin-form-container">
    <div class="form-header">
        <h2>{{ action }} Categoría</h2>
    </div>

    <form method="post" class="admin-form">
        {% csrf_token %}

        {% if form.errors %}
        <div class="alert alert-danger">
            Por favor corrige los errores abajo.
        </div>
        {% endif %}

        <div class="form-group">
            <label for="parent" class="form-label">Categoría Padre (opcional)</label>
            <select name="parent" id="parent" class="form-select">
                <option value="">-- Sin padre (categoría principal) --</option>
                {% for cat in form.fields.parent.queryset %}
                <option value="{{ cat.pk }}" {% if form.instance.parent_id == cat.pk %}selected{% endif %}>
                    {{ cat.name }}
                </option>
                {% endfor %}
            </select>
            {% if form.parent.errors %}
            <div class="error-feedback">{{ form.parent.errors.0 }}</div>
            {% endif %}
        </div>

        <div class="form-group">
            <label for="name" class="form-label">Nombre</label>
            <input type="text" name="name" id="name" class="form-input" value="{{ form.instance.name|default:'' }}">
            {% if form.name.errors %}
            <div class="error-feedback">{{ form.name.errors.0 }}</div>
            {% endif %}
        </div>
        
        <div class="form-group" style="margin-top: 15px;">
            <label class="form-checkbox-label">
                <input type="checkbox" name="is_active" {% if form.instance.is_active %}checked{% endif %}>
                Categoría Activa
            </label>
            <small class="form-text text-muted">Las categorías inactivas no se muestran en el catálogo.</small>
        </div>

        {% if action == 'Editar' %}
        <div class="attributes-section" style="margin-top: 30px; border-top: 1px solid #eee; padding-top: 20px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                <h3 style="margin: 0;">Atributos de Categoría</h3>
                <a href="{% url 'admin_category_attribute_create' form.instance.pk %}" class="btn btn-sm btn-outline">+ Nuevo
                    Atributo</a>
            </div>

            {% if form.instance.attributes.exists %}
            <table class="admin-table">
                <thead>
                    <tr>
                        <th>Nombre</th>
                        <th>Slug</th>
                        <th>Tipo</th>
                        <th>Requerido</th>
                        <th>Acciones</th>
                    </tr>
                </thead>
                <tbody>
                    {% for attr in form.instance.attributes.all %}
                    <tr>
                        <td>{{ attr.name }}</td>
                        <td><code>{{ attr.slug }}</code></td>
                        <td>{{ attr.get_type_display }}</td>
                        <td>
                            {% if attr.required %}
                            <span class="badge badge-success">Sí</span>
                            {% else %}
                            <span class="badge badge-secondary">No</span>
                            {% endif %}
                        </td>
                        <td class="actions">
                            <a href="{% url 'admin_category_attribute_edit' form.instance.pk attr.pk %}" class="btn btn-outline"
                                title="Editar">✏️</a>
                            <a href="{% url 'admin_category_attribute_delete' form.instance.pk attr.pk %}" class="btn btn-danger-outline"
                                title="Eliminar">🗑️</a>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% else %}
            <div class="alert alert-info">
                Esta categoría no tiene atributos definidos. Agregar atributos permite filtros específicos.
            </div>
            {% endif %}
        </div>
        {% endif %}

        <div class="form-actions">
            <a href="{% url 'admin_category_list' %}" class="btn btn-outline">Cancelar</a>
            <button type="submit" class="btn btn-primary">Guardar Categoría</button>
        </div>
    </form>
</div>
{% endblock %}
"""

file_path = r'c:\Users\Brian\Desktop\webflexs\admin_panel\templates\admin_panel\categories\form.html'
with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)
print(f"Successfully wrote to {file_path}")
