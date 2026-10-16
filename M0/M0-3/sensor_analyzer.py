#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sensor_analyzer.py  —— 上一届学长留下的"能用"的脚本

注释（学长原话）：
    "处理一下传感器数据就能用"

原本意图：
    1. 读取 sensor_data.csv（列：time, value）
    2. 计算 value 的平均值、标准差
    3. 剔除离群值（|value - mean| > 2 * std）
    4. 把清洗后的数据保存为 cleaned_data.csv
    5. 打印一份统计摘要

现状：跑不通 / 跑出来数不对。就交给你了。
"""

import csv
import argparse
import math
import sys


def load_data(path):
    """读 csv,返回 (times, values)。列名不符/空文件/非数值时抛可读异常。"""
    times, values = [], []
    with open(path) as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:                      # 文件完全空(连表头都没有)
            raise ValueError("输入文件为空: %s" % path)
        missing = [c for c in ("time", "value") if c not in reader.fieldnames]
        if missing:                                        # 列名不符
            raise KeyError("csv 缺少列 %s,实际列为: %s" % (missing, reader.fieldnames))
        for i, row in enumerate(reader, start=2):          # start=2:表头算第 1 行
            try:
                times.append(float(row["time"]))
                values.append(float(row["value"]))
            except ValueError:
                raise ValueError("第 %d 行: 无法把 time=%r value=%r 转为数字"
                                 % (i, row["time"], row["value"])) from None
    if not values:                                         # 只有表头,没有数据行
        raise ValueError("数据为空(只有表头): %s" % path)
    return times, values

def analyze(values):
    """计算均值与总体标准差(除以 n)。"""
    n = len(values)
    mean = sum(values) / n
    acc = 0
    for v in values:
        acc += (v-mean) ** 2
    std = math.sqrt(acc / n)
    return mean, std

def clean(times, values, mean, std):
    """剔除 |v-mean| > 2*std 的离群值,返回 (time, value) 配对列表。"""
    if std == 0:                                           # 全常数列:剔除无意义,跳过但不崩
        print("提示: 标准差为 0(全部数值相同),跳过离群值剔除", file=sys.stderr)
        return list(zip(times, values))

    cleaned = []
    for t, v in zip(times, values):
        if abs(v - mean) <= 2 * std:
            cleaned.append((t, v))
    return cleaned

def main():
    parser = argparse.ArgumentParser(description="传感器数据清洗与统计")
    parser.add_argument("--input", default="sensor_data.csv", help="输入 csv 路径")
    parser.add_argument("--output", default="cleaned_data.csv", help="输出 csv 路径")
    args = parser.parse_args()

    times, values = load_data(args.input)
    mean, std = analyze(values)
    cleaned = clean(times, values, mean, std)

    with open(args.output, "w", newline="") as f:          # newline="" 防止 Windows 式空行
        writer = csv.writer(f)
        writer.writerow(["time", "value"])
        for t, v in cleaned:
            writer.writerow([t, v])

    print("共读取 %d 条数据" % len(values))
    print("均值 mean = %.4f" % mean)
    print("标准差 std = %.4f" % std)
    print("清洗后剩余 %d 条" % len(cleaned))
    print("已保存到 %s" % args.output)


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as e:
        print("错误: 找不到文件 %s" % e.filename, file=sys.stderr)
        sys.exit(1)
    except KeyError as e:
        print("错误: %s" % e, file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print("错误: %s" % e, file=sys.stderr)
        sys.exit(1)
