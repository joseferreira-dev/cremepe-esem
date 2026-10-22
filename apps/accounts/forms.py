from django import forms

from .models import normalize_cpf


class LoginByCpfForm(forms.Form):
    cpf = forms.CharField(
        label="CPF",
        max_length=14,
        widget=forms.TextInput(
            attrs={
                "class": "campo",
                "placeholder": "000.000.000-00",
                "autofocus": True,
                "inputmode": "numeric",
            }
        ),
    )
    senha = forms.CharField(
        label="Senha",
        widget=forms.PasswordInput(attrs={"class": "campo"}),
    )

    def clean_cpf(self) -> str:
        cpf = normalize_cpf(self.cleaned_data["cpf"])
        if len(cpf) != 11:
            raise forms.ValidationError("Informe um CPF com 11 dígitos.")
        return cpf
