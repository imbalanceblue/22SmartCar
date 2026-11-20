# 位姿片段目录

当前版本**不启用外部位姿文件**：位姿直接内联在 YAML 里（`pose: [x, y, yaw]`），
原因是不依赖 `!include` 这类自定义 YAML 构造器，考官拿到的任何 YAML 都能直接解析。

需要复用同一组位姿时,把坐标写在 `config` 里或在 YAML 里重复一次即可,
详见 `docs/dsl.md` 的「位姿来源」一节。

若后续要支持外部位姿文件,约定如下(尚未实现,勿在考试 YAML 里使用):

```yaml
- name: navigate_to_scan
  type: NavigateTo
  pose_file: poses/scan_zone.yaml    # 文件内容为 {x: 2.0, y: 0.5, yaw: 0.0}
```
