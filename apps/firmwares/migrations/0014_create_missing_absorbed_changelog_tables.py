from django.db import migrations


def create_missing_tables(apps, schema_editor):
    models = apps.get_models()
    firmware_models = {
        model.__name__: model
        for model in models
        if model._meta.app_label == "firmwares"
    }
    existing_tables = set(schema_editor.connection.introspection.table_names())

    for model_name in ("ChangelogEntry", "ReleaseNote", "FirmwareDiff"):
        model = firmware_models[model_name]
        table_name = model._meta.db_table
        if table_name not in existing_tables:
            schema_editor.create_model(model)
            existing_tables.add(table_name)


class Migration(migrations.Migration):
    dependencies = [
        (
            "firmwares",
            "0013_remove_oemsource_brand_remove_scraperconfig_source_and_more",
        ),
    ]

    operations = [
        migrations.RunPython(
            create_missing_tables,
            # Preserve tables that may have existed before the models were absorbed.
            reverse_code=migrations.RunPython.noop,
        ),
    ]
