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
        for row in reader:
            xs.append(float(row[col_x]))
            ys.append(float(row[col_y]))
    return xs, ys

def pearson_r(xs, ys):
    """计算相关系数"""
    n = len(xs)
    
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
    main()
    

