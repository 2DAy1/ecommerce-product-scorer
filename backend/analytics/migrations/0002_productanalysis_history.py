import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0001_initial"),
        ("catalog", "0001_initial"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="productanalysis",
            options={"ordering": ["-calculated_at", "-pk"]},
        ),
        migrations.AlterField(
            model_name="productanalysis",
            name="product",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="analyses",
                to="catalog.product",
            ),
        ),
        migrations.AddIndex(
            model_name="productanalysis",
            index=models.Index(
                fields=["product", "-calculated_at"],
                name="an_analysis_product_calc_idx",
            ),
        ),
    ]
