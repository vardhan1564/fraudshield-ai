"""Model cards for every served model (spec 03 §1, §9), built from saved artifacts only."""

from pathlib import Path

import yaml

from ml.pipelines.model_cards import SECTIONS, build_all
from ml.tests.conftest import config_for


def test_every_model_gets_a_complete_card(served_folder: Path, tmp_path: Path) -> None:
    cfg = yaml.safe_load(config_for(served_folder).read_text(encoding="utf-8"))
    before = {f.name: f.stat().st_mtime for f in served_folder.iterdir()}

    written = build_all(served_folder, cfg, mlflow=None, out_dir=tmp_path)

    assert set(written) == {"txn_model", "anomaly_model", "social_model", "meta_model"}
    for name, (path, version) in written.items():
        card = path.read_text(encoding="utf-8")
        assert path == tmp_path / name / "MODEL_CARD.md"
        assert card.startswith(f"# Model card: {name}")
        for section in SECTIONS:
            assert f"## {section}" in card, (name, section)
        assert "(none)" not in card and "nan" not in card.lower().split("financ")[0]
        assert version in card
    meta = (tmp_path / "meta_model" / "MODEL_CARD.md").read_text(encoding="utf-8")
    assert "HOLD if risk_score >" in meta and "> 0.80 (served risk_score)" in meta
    assert "IEEE-CIS benchmark: no run found" in (tmp_path / "txn_model/MODEL_CARD.md").read_text(
        encoding="utf-8"
    )
    # Cards never retrain or rewrite a served model.
    assert {f.name: f.stat().st_mtime for f in served_folder.iterdir()} == before
