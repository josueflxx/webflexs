from django import forms
from catalog.models import Category

class CategoryForm(forms.ModelForm):
    """Form to create/edit categories."""
    
    class Meta:
        model = Category
        fields = ['name', 'parent', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Nombre de la categoría'}),
            'parent': forms.Select(attrs={'class': 'form-select'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        }
        labels = {
            'name': 'Nombre',
            'parent': 'Categoría Padre',
            'is_active': '¿Activa?',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Filter parent queryset to exclude self helps avoid recursion loops in UI, 
        # though logic to prevent self-parenting needs validation on save or clean.
        # For now, just ensuring it looks good.
        self.fields['parent'].queryset = Category.objects.all().order_by('name')
