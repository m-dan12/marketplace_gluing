from tools.brand_audit import crosstab, render


def row(acc, pb, lb, offer, stock=1, title=""):
    return {"account": acc, "plan_brand": pb, "live_brand": lb, "offer_id": offer, "stock": stock, "title": title}


def test_crosstab_marks_minority_as_mismatch():
    rows = [row("skazka", "Анна Мария PA", "Нет бренда", f"PA{i}") for i in range(5)] + \
           [row("skazka", "Анна Мария PA", "Сказка", "PA6273", 0, "Сказка Венера"), row("skazka", "Сказка", "Сказка", "PT1")]
    t = crosstab(rows)
    bad = [a for a in t if not a["main"]]
    assert len(bad) == 1 and bad[0]["live_brand"] == "Сказка" and bad[0]["plan_brand"] == "Анна Мария PA"
    assert bad[0]["n"] == 1 and bad[0]["in_stock"] == 0
    assert "расхождение" in render(t) and "расходятся с основным брендом ветки: 1" in render(t)


def test_no_mismatch_when_uniform():
    t = crosstab([row("a", "Сказка", "Сказка", "PT1"), row("a", "Сказка", "Сказка", "PT2")])
    assert all(a["main"] for a in t)
