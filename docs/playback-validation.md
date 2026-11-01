# 回放与预览恢复验证

实现前列出的隔离验证失败场景：

- 全屏切换产生短暂隐藏，误停全部实时码流；页面真正切走时又没有停止拉流。
- 预览仍启用，但某路播放器意外退出且未排队重连；自动恢复误启动已手动停止或隐藏的页面。
- 解码帧计数增加，播放时钟却冻结；首次连接失败或重连时组件抛出异常，恢复链中断。
- 暂停被误判为卡帧；旧定位或旧通道的异步回调覆盖新状态。
- 时间轴落在录像空缺、当天边界、重叠片段或最后一段末尾；跨段续播丢失暂停和倍速。
- 16× 未传递到 RTSP Scale，设备拒绝倍速却被显示为正常播放。
- 全屏视频吞掉单击和键盘事件；输入日期、时间或选择框时误触回放快捷键。
- 局部放大超出视频边界，重连或退出全屏后丢失放大中心。
- 人脸检测未出帧、截图尚未写完、检测失败、用户取消、切换通道或旧检测任务晚到；将抽样检测误称为身份识别或完整检索。

默认验证：`QT_QPA_PLATFORM=offscreen TMPDIR="$PWD/.tmp" .venv/bin/pytest -q --junitxml=artifacts/private/verification/pytest.xml`。

原生 E2E：`TMPDIR="$PWD/.tmp" .venv/bin/python scripts/verify_playback_controls.py`。使用合成视频、实际 VLC 渲染及 Qt 输入，输出 JSON 和界面截图到 `artifacts/private/playback-controls/`。真实设备另运行 `scripts/verify_workflow.py`；合成视频结果不能证明 NVR 接受 16×。
