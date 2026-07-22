from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("ai", "0003_conversation_message"),
    ]

    operations = [
        migrations.RenameField(
            model_name="conversation",
            old_name="buyer",
            new_name="user",
        ),
    ]
