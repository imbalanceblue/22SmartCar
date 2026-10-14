# M0-1 从零搭建开发环境

## 任务概述

本任务目标：在 VirtualBox 虚拟机内安装 Ubuntu22.04，搭建 ROS2 Humble 开发环境，配置 VSCode 编辑器与开发插件，完成 Git 本地身份配置，搭建智能车项目基础开发环境。

### 实现环境：VirtualBox + Ubuntu 22.04

### AI 使用说明：本文档由 AI 辅助撰写；环境搭建思路、命令校验、故障排查参考 AI 给出方案，所有命令均在本机虚拟机内亲手执行验证

### 操作资料



1. B 站 Ubuntu+ROS2 Humble 环境搭建教程

2. ROS2 Humble 官方文档（国内镜像安装部分）

   [https://docs.ros.org/en/humble/Installation/Ubuntu-Install.html](https://docs.ros.org/en/humble/Installation/Ubuntu-Install.html)

3. Anaconda 官方文档

   [https://www.anaconda.com/docs/getting-started/welcome](https://www.anaconda.com/docs/getting-started/welcome)

4. Git 官方文档

   [https://git-scm.com/book/zh/v2](https://git-scm.com/book/zh/v2)

5. Ubuntu 软件源与 VSCode 安装方式说明

### 操作流程简述



1. 在 VirtualBox 新建虚拟机，安装 Ubuntu22.04 系统

2. 安装 VirtualBox 增强插件，配置虚拟机与物理机之间双向复制粘贴、文件拖拽

3. 配置虚拟机网络为 NAT 模式，更新系统软件源，安装 ROS2 Humble，配置 python 环境变量

4. 使用微软官方 apt 源安装 VSCode（deb 版，非 Snap），安装开发插件：Python、C/C++、ROS、简体中文语言包

5. 测试 VSCode 内置终端能否正常读取 ROS2 环境变量

6. 安装 Git，配置本地 Git 用户名与邮箱，完成版本控制基础配置



***

## 二、本机踩坑实录

下面是本次环境搭建过程中实际遇到的坑，按时间顺序记录

### 坑 1：虚拟机无法实现物理机和虚拟机之间复制粘贴



* 现象：物理机复制文字，无法粘贴进 Ubuntu 虚拟机；虚拟机内复制的内容，也无法粘贴到外面 Windows 主机。

* 定位方法：检查 VirtualBox 设置，已经开启「双向剪贴板」，但依然无效。查阅资料得知：仅在虚拟机设置里勾选剪贴板不够，虚拟机内部系统还必须安装 VirtualBox 客户机增强插件（VBoxGuestAdditions）。

* 报错 / 现象原文：剪贴板操作完全隔离，两边内容互不互通。

* 解决方案：

  虚拟机窗口顶部菜单：设备 → 安装增强功能。

  在 Ubuntu 内挂载光盘，进入光盘目录执行安装脚本，重启虚拟机。重启后双向复制粘贴生效。



```
cd /media/$USER/VBox_GAs_*
sudo ./VBoxLinuxAdditions.run
```



* 补充备注：如果后续失效，优先确认虚拟机增强模块是否正常加载（`lsmod | grep vboxguest`）。



***

### 坑 2：ros2 --version 命令报错



* 报错原文：`error: unrecognized arguments: --version`

* 现象：想通过`ros2 --version`查看 ROS 版本，提示参数不识别。

* 定位原因：ROS2 Humble 本身没有`--version`这个命令参数，属于命令行记错。

* 解决方法：改用`echo $ROS_DISTRO`查看发行版本，或者`ros2 --help`查看帮助信息。



***

### 坑 3：桥接 Wi-Fi 模式下 DNS 解析失败、网络不可达



* 现象：虚拟机网络设置为「桥接网卡」、桥接到主机 Wi-Fi 网卡（Intel Wi-Fi 6E AX211）后，`apt update`报「暂时不能解析域名 [mirrors.ustc.edu.cn](https://mirrors.ustc.edu.cn)」，`ping ``223.5.5.5`直接返回「网络不可达」，写死 resolv.conf 后依然是「域名解析出现暂时性错误」。

* 定位原因：VirtualBox 对 Wi-Fi 网卡的桥接支持不稳定，无线桥接驱动无法正确转发 DHCP 与 DNS 请求，导致虚拟机根本拿不到网络出口，连网关都到不了。

* 解决方案：

1. 虚拟机内正常关机：`sudo poweroff`

2. VirtualBox 管理器 → 设置 → 网络，把连接方式从「桥接网卡」改为「网络地址转换 (NAT)」

3. 启动虚拟机，网络立即恢复

4. （可选，推荐）写死公共 DNS 防止后续再抽风：



```
sudo systemctl disable --now systemd-resolved
sudo rm -f /etc/resolv.conf
printf "nameserver 223.5.5.5\nnameserver 8.8.8.8\n" | sudo tee /etc/resolv.conf
```



* 验证：



```
ping -c 3 223.5.5.5              # 应能收到 64 bytes 回复
ping -c 3 packages.microsoft.com  # 应能解析并 ping 通
```



* 补充备注：NAT 模式下虚拟机通过 Windows 主机上网，无需从路由器拿 IP，是 VirtualBox 里最稳的网络方式。仅当后续需要与实体机器人在同一局域网直接通信（ROS 多机通信、实体车调试）时，才切回桥接模式。



***

### 坑 4：Snap 版 VSCode「另存为」对话框完全空白（最终换 deb 版解决）



* 现象：通过`snap install --classic code`装好 VSCode 后，点击「文件 → 另存为」，系统弹出的 GTK 文件选择对话框完全空白 —— 路径栏、左侧快捷位置、按钮文字、图标全部不显示，只剩几个空框，无法浏览文件夹、无法输入文件名。

* 定位原因：Snap 版 VSCode 默认调用系统 xdg-desktop-portal 的 GTK 文件选择器，在 VirtualBox 虚拟机环境下该门户组件渲染异常（缺字体、缺 portal 服务、Wayland/X11 兼容问题）。

* 解决方案：彻底放弃 Snap 版，改装微软官方 deb 版：



```
# 1. 卸载 Snap 版
sudo snap remove code

# 2. 安装依赖
sudo apt update
sudo apt install -y wget gpg

# 3. 导入微软签名密钥
wget -qO- https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > /tmp/packages.microsoft.gpg
sudo install -D -o root -g root -m 644 /tmp/packages.microsoft.gpg /etc/apt/keyrings/packages.microsoft.gpg

# 4. 添加 VSCode 官方 apt 源
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/packages.microsoft.gpg] https://packages.microsoft.com/repos/vscode stable main" | sudo tee /etc/apt/sources.list.d/vscode.list

# 5. 安装 deb 版 VSCode
sudo apt update
sudo apt install -y code

# 6. 验证
code --version
```



* 附带收益：

1. deb 版无 Snap 沙箱隔离，内置终端可直接读取 ROS 环境变量（`printenv | grep ROS`直接返回`ROS_DISTRO=humble`），无需每次手动`source /opt/ros/humble/setup.bash`。

2. Snap 版与 deb 版共享`~/.config/Code`和`~/.vscode/extensions`，原来装的中文语言包、Python、C/C++ 等插件会自动继承，无需重装。

3. 以后`sudo apt upgrade`会自动更新 VSCode，不用再手动下载安装包。

* 补充备注：曾经考虑过的临时方案（开启 VSCode 的`files.simpleDialog.enable: true`绕开系统对话框）只作为应急手段，长期开发仍推荐 deb 版。



***

## 三、最终验证清单



| 验证项         | 命令 / 操作                                 | 预期结果                     |
| ----------- | --------------------------------------- | ------------------------ |
| ROS 版本      | `echo $ROS_DISTRO`                      | 输出`humble`               |
| 网络连通        | `ping -c 3 223.5.5.5`                   | 0% 丢包                    |
| VSCode 版本   | `code --version`                        | 输出`1.141.0`或更高           |
| VSCode 另存为  | 文件 → 另存为                                | 对话框正常显示文件夹列表             |
| 内置终端 ROS 环境 | VSCode 内 Ctrl+`→`printenv \| grep ROS\` | 返回`ROS_DISTRO=humble`等变量 |
| 增强功能        | `lsmod \| grep vboxguest`               | 有输出                      |
| 双向剪贴板       | Win 复制 → 虚拟机粘贴                          | 正常                       |



***

## 四、日常使用命令备忘



```
# 启动 VSCode（在项目目录下）
cd ~/your_ws
code .

# 激活 ROS 环境（如果内置终端没自动 source）
source /opt/ros/humble/setup.bash

# 编译 ROS 2 工作空间
cd ~/your_ws
colcon build
source install/setup.bash

# 更新系统和 VSCode
sudo apt update && sudo apt upgrade