# Generated migration for InstructionTemplate model

from django.db import migrations, models
import uuid


def create_default_templates(apps, schema_editor):
    """Create default instruction templates."""
    InstructionTemplate = apps.get_model('knowledge', 'InstructionTemplate')
    
    defaults = [
        {
            "label": "Professional & Friendly Tone",
            "message": "Always maintain a professional yet friendly tone. Be helpful and approachable while keeping responses concise and to the point.",
            "description": "Sets a balanced professional tone",
            "category": "tone"
        },
        {
            "label": "Casual & Conversational",
            "message": "Use a casual, conversational tone. Feel free to use contractions and friendly language. Make the customer feel like they're chatting with a helpful friend.",
            "description": "Relaxed, friendly communication style",
            "category": "tone"
        },
        {
            "label": "Empathetic Support",
            "message": "Show empathy and understanding in every response. Acknowledge the customer's feelings and concerns before providing solutions. Use phrases like 'I understand how frustrating that must be' or 'I appreciate your patience'.",
            "description": "Empathetic and understanding responses",
            "category": "behavior"
        },
        {
            "label": "Brief Responses Only",
            "message": "Keep all responses extremely brief - maximum 2-3 sentences unless specifically asked for more detail. Get straight to the point without unnecessary elaboration.",
            "description": "Short, concise answers",
            "category": "formatting"
        },
        {
            "label": "Detailed Explanations",
            "message": "Provide thorough, detailed explanations. Break down complex topics into easy-to-understand steps. Use examples when helpful and ensure the customer fully understands before moving on.",
            "description": "Comprehensive, detailed responses",
            "category": "formatting"
        },
        {
            "label": "Use Bullet Points",
            "message": "When listing information or multiple points, always use bullet points or numbered lists for clarity. Structure responses to be scannable and easy to read.",
            "description": "Structured, list-based formatting",
            "category": "formatting"
        },
        {
            "label": "Proactive Problem Solver",
            "message": "Don't just answer the immediate question - anticipate related questions or issues the customer might have and address them proactively. Offer additional helpful information when relevant.",
            "description": "Anticipates customer needs",
            "category": "behavior"
        },
        {
            "label": "Escalation Guidelines",
            "message": "If a customer is frustrated, has a complex technical issue, or specifically requests human assistance, acknowledge their concern and let them know you're connecting them with a team member who can better assist. Never make promises about specific timelines or outcomes.",
            "description": "When and how to escalate",
            "category": "behavior"
        },
        {
            "label": "Product Expert",
            "message": "You are a product expert. Always reference specific features, benefits, and use cases when discussing products. If you don't have information about something, admit it clearly rather than guessing.",
            "description": "Product knowledge focus",
            "category": "behavior"
        },
        {
            "label": "Privacy & Security Conscious",
            "message": "Never ask for sensitive information like passwords, credit card numbers, or social security numbers in chat. If such information is needed, direct the customer to secure channels. Always respect customer privacy.",
            "description": "Handles sensitive data appropriately",
            "category": "behavior"
        },
    ]
    
    for template_data in defaults:
        InstructionTemplate.objects.get_or_create(
            label=template_data["label"],
            defaults=template_data
        )


def remove_default_templates(apps, schema_editor):
    """Remove default templates (for rollback)."""
    InstructionTemplate = apps.get_model('knowledge', 'InstructionTemplate')
    InstructionTemplate.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('knowledge', '0004_custominstruction'),
    ]

    operations = [
        migrations.CreateModel(
            name='InstructionTemplate',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('label', models.CharField(help_text='Template name/label', max_length=255)),
                ('message', models.TextField(help_text='Template instruction content')),
                ('description', models.TextField(blank=True, default='', help_text='Brief description of what this instruction does')),
                ('category', models.CharField(default='general', help_text="Category for organizing templates (e.g., 'tone', 'behavior', 'formatting')", max_length=50)),
                ('is_active', models.BooleanField(default=True, help_text='Whether this template is available for use')),
                ('usage_count', models.IntegerField(default=0, help_text='Number of times this template has been used')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['-usage_count', 'label'],
                'indexes': [
                    models.Index(fields=['category', 'is_active'], name='knowledge_i_categor_idx'),
                ],
            },
        ),
        migrations.RunPython(create_default_templates, remove_default_templates),
    ]

