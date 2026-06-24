---
nome: "{{ nome }}"
nicho: "{{ nicho }}"
localizacao: "{{ localizacao }}"
url: "{{ url }}"
score: {{ score }}
status: "{{ status }}"
tags: [{{ tags | join(', ') }}]
---

# Auditoria Comercial: {{ nome }}

## 🚨 Problemas Identificados
{% for p in problemas %}* {{ p }}
{% endfor %}
## 💰 Impacto Estimado (Revenue Leak)
{{ impacto }}

## ✉️ Draft de Abordagem
**Assunto:** {{ email_assunto }}
**Mensagem:**
{{ email_mensagem }}

## 📝 Dados Técnicos Extraídos
* **Email:** {{ email }}
* **Telefone:** {{ telefone }}
* **Redes:** {{ redes_sociais }}
* **Tempo de Carregamento:** {{ load_time }}s
* **Tem Booking/Formulário:** {{ tem_booking }}
* **Tem WhatsApp:** {{ tem_whatsapp }}
