from django.db import migrations

INDEX_NAME = "ai_productembedding_embedding_hnsw"


def create_hnsw_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        f"CREATE INDEX {INDEX_NAME} ON ai_productembedding "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def drop_hnsw_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")


class Migration(migrations.Migration):

    dependencies = [
        ("ai", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_hnsw_index, drop_hnsw_index),
    ]
