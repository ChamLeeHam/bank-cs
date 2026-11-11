"""
下载 Banking77 原始数据（PolyAI/banking77，CC-BY-4.0）并保存为 CSV

运行（在项目根目录）：
    uv run python -m src.scripts.download_banking77
"""
from datasets import load_dataset

from src.schema.config import get_config


def main() -> None:
    raw_dir = get_config().data_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    ds = load_dataset("PolyAI/banking77")
    label_names = ds["train"].features["label"].names  # 77 个标签名
    print(ds)
    print(f"{len(label_names)} labels, e.g.: {label_names[:10]}")

    for split in ("train", "test"):
        df = ds[split].to_pandas()
        df["category"] = df["label"].map(lambda i: label_names[i])
        out = raw_dir / f"banking77_{split}.csv"
        df.to_csv(out, index=False, encoding="utf-8")
        print(f"saved {len(df)} rows -> {out}")


if __name__ == "__main__":
    main()