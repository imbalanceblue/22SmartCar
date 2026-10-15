import yaml
import csv
import math
import sys

def load_config(path):
    """读 yaml 配置文件,返回配置字典"""
    with open(path) as f:
        return yaml.safe_load(f)

def load_columns(csv_path, col_x, col_y):
    """读 csv,返回 (xs, ys) 两个浮点列表"""
    xs = []
    ys = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is not None and (col_x not in reader.fieldnames or col_y not in reader.fieldnames):
            # 空文件/缺列都在这里报,信息里带上"实际有哪些列"
            raise KeyError(f"csv 中没有列 '{col_x}' 或 '{col_y}',实际列为: {reader.fieldnames}")
        for i, row in enumerate(reader, start=2):   # start=2:表头是第 1 行,数据从第 2 行起
            try:
                xs.append(float(row[col_x]))
                ys.append(float(row[col_y]))
            except ValueError:
                # 提示第几行、哪个值转不了
                raise ValueError(f"第{i}行：无法把 x={row[col_x]} 或 y={row[col_y]}转为数字") from None     # from None:不显示原始链,输出干净
    return xs, ys

def pearson_r(xs, ys):
    """计算相关系数"""
    n = len(xs)
    if n == 0:
        raise ValueError("数据为空,无法计算相关系数")
    
    sum_x = 0.0
    sum_y = 0.0
    for i in range(n):
        sum_x = sum_x + xs[i]
        sum_y = sum_y + ys[i]

    mean_x = sum_x / n
    mean_y = sum_y / n

    dx = 0.0
    dy = 0.0
    prod = 0.0
    for i in range(n):
        a = xs[i] - mean_x
        b = ys[i] - mean_y
        dx = dx + a * a
        dy = dy + b * b
        prod = prod + a * b

    if dx == 0 or dy == 0:
        raise ZeroDivisionError("某列方差为 0,相关系数无定义")
    denom = math.sqrt(dx * dy)
    r = prod / denom
    return r

def main():
    """主流程:读配置 → 读数据 → 计算 → 输出"""
    cfg = load_config("config.yaml")
    csv_path = cfg["input_csv"]
    col_x = cfg["columns"]["x"]
    col_y = cfg["columns"]["y"]

    xs, ys = load_columns(csv_path, col_x, col_y)
    r = pearson_r(xs, ys)

    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    print("n =", n)
    print("mean_x =", mean_x)
    print("mean_y =", mean_y)
    print("r =", r)

if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as e:
        print(f"错误:找不到文件 {e.filename}", file=sys.stderr)
        sys.exit(1)
    except KeyError as e:
        print(f"错误:{e}", file=sys.stderr)
        sys.exit(1)
    except (ValueError, ZeroDivisionError) as e:
        print(f"错误:{e}", file=sys.stderr)
        sys.exit(1)
    

