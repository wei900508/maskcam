################################################################################
# Copyright (c) 2020-2021, Berkeley Design Technology, Inc. All rights reserved.
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.  IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.
################################################################################

import os
import json

import streamlit as st

from datetime import datetime, time, timezone
from session_manager import get_state
from utils.api_utils import (
    get_device,
    get_devices,
    get_statistics_from_to,
    get_device_files,
)
from utils.format_utils import create_chart, format_data

from paho.mqtt import client as mqtt_client

MQTT_BROKER = os.environ["MQTT_BROKER"]
MQTT_BROKER_PORT = int(os.environ["MQTT_BROKER_PORT"])
MQTT_CLIENT_ID = os.environ["MQTT_CLIENT_ID"]

MQTT_TOPIC_COMMANDS = "commands"
MQTT_TOPIC_STATUS = "device-status"

CMD_FILE_SAVE = "save_file"
CMD_STREAMING_START = "streaming_start"
CMD_STREAMING_STOP = "streaming_stop"
CMD_INFERENCE_RESTART = "inference_restart"
CMD_FILESERVER_RESTART = "fileserver_restart"
CMD_REQUEST_STATUS = "status_request"

state = get_state()


def display_sidebar(all_devices, state):
    """
    Display sidebar information.
    """
    st.sidebar.subheader("設備選擇")
    state.selected_device = st.sidebar.selectbox(
        "選擇的設備",
        all_devices,
        index=all_devices.index(state.selected_device if state.selected_device else None),
    )

    st.sidebar.subheader("過濾器")
    state.date_filter = st.sidebar.date_input(
        "起始日期 - 結束日期",
        (
            state.date_filter[0]
            if state.date_filter and len(state.date_filter) == 2
            else datetime.now(timezone.utc),
            state.date_filter[1]
            if state.date_filter and len(state.date_filter) == 2
            else datetime.now(timezone.utc),
        ),
    )
    first_column, second_column = st.sidebar.beta_columns(2)
    state.from_time = first_column.time_input(
        "起始日期", state.from_time if state.from_time else time(0, 0)
    )
    state.to_time = second_column.time_input(
        "結束日期", state.to_time if state.to_time else time(23, 45)
    )

    state.group_data_by = st.sidebar.selectbox(
        "分組數據",
        ["秒", "分鐘", "小時", "天", "周", "月"],
        index=2,
    )

    state.show_only_one_chart = st.sidebar.checkbox("只顯示一張圖表", value=True)


def display_device(state):
    """
    Display specific device information.
    """
    selected_device = state.selected_device
    device = get_device(selected_device)

    if device is None:
        st.write("獲取設備訊息時似乎出了點問題。")
    else:
        st.header(f"Device: {device['id']}")

        if state.mqtt_last_status:
            status = state.mqtt_last_status  # shortcut
            if not status["device_address"]:
                st.write(
                    ":warning: **Set MASKCAM_DEVICE_ADDRESS on the device to enable "
                    "streaming and file download links**"
                )
            device_status = st.beta_container()
            col1, col2 = device_status.beta_columns(2)
            col1.write("🟢 裝置已連接 " f"*(最後更新時間: {status['time']})*")
            if not status["streaming_address"] or status["streaming_address"] == "N/A":
                col2.write(":red_circle: 串流已停止")
            else:
                if status["device_address"]:
                    col2.write(
                        f"🟢 <a href=\"{status['streaming_address']}\" target=\"_blank\">"
                        "Streaming enabled</a>",
                        unsafe_allow_html=True,
                    )
                else:
                    col2.write(
                        "🟢 串流已啟用 (unknown device address)",
                    )
            device_status.write(
                f"**儲存的影片: {status['save_current_files']}**"
                f" | *Inference runtime: {status['inference_runtime']}*"
                f" | *Fileserver runtime: {status['fileserver_runtime']}*"
            )

        mqtt_status = st.empty()  # Might be changed in real time during connection
        if not state.mqtt_last_status:
            if not state.mqtt_status:
                # Loading page, first connection attempt to device
                send_mqtt_command(device["id"], CMD_REQUEST_STATUS, mqtt_status)
            else:
                mqtt_set_status(mqtt_status, state.mqtt_status)

        cols = st.beta_columns(6)
        # Buttons from right to left
        with cols.pop():
            if st.button("重啟 Deepstream"):
                send_mqtt_command(device["id"], CMD_INFERENCE_RESTART, mqtt_status)
        with cols.pop():
            if st.button("重啟文件伺服器"):
                send_mqtt_command(device["id"], CMD_FILESERVER_RESTART, mqtt_status)
        with cols.pop():
            if st.button("停止串流"):
                send_mqtt_command(device["id"], CMD_STREAMING_STOP, mqtt_status)
        with cols.pop():
            if st.button("開始串流"):
                send_mqtt_command(device["id"], CMD_STREAMING_START, mqtt_status)
        with cols.pop():
            if st.button("保存影片"):
                send_mqtt_command(device["id"], CMD_FILE_SAVE, mqtt_status)
        with cols.pop():
            if st.button("刷新狀態"):
                send_mqtt_command(device["id"], CMD_REQUEST_STATUS, mqtt_status)

        st.header("報告的統計數據")
        device_statistics = None
        date_filter = state.date_filter

        if len(date_filter) == 2:
            datetime_from = f"{date_filter[0]}T{state.from_time}"
            datetime_to = f"{date_filter[1]}T{state.to_time}"
            device_statistics = get_statistics_from_to(selected_device, datetime_from, datetime_to)

        if not device_statistics:
            st.write("所選設備沒有可顯示給定過濾器的統計信息。")
        else:
            reports, alerts = format_data(device_statistics, state.group_data_by)

            if state.show_only_one_chart:
                complete_chart = create_chart(reports=reports, alerts=alerts)
                st.plotly_chart(complete_chart, use_container_width=True)
            else:
                st.subheader("Reports")
                if reports:
                    report_chart = create_chart(reports=reports)
                    st.plotly_chart(report_chart, use_container_width=True)
                else:
                    st.write("The selected device has no reports to show for the given filters.")

                st.subheader("Alerts")
                if alerts:
                    alerts_chart = create_chart(alerts=alerts)
                    st.plotly_chart(alerts_chart, use_container_width=True)
                else:
                    st.write("The selected device has no alerts to show for the given filters.")
        device_files = get_device_files(device_id=selected_device)
        st.subheader("在設備上保存的影片")
        if not device_files:
            st.write("所選設備還沒有保存的文件")
        else:
            server_address = None
            if not state.mqtt_last_status:
                st.write(":warning: **Downloads will fail since device is NOT connected**")
            elif state.mqtt_last_status["device_address"] is None:
                st.write(
                    ":warning: **Set MASKCAM_DEVICE_ADDRESS on device to enable download links**"
                )
            else:  # file_server_address is valid when device_address=MASKCAM_DEVICE_ADDRESS is set
                server_address = f"{device['file_server_address']}"
            for file_instance in device_files:
                if server_address:
                    url = f"{server_address}/{file_instance['video_name']}"
                    st.markdown(f"[{file_instance['video_name']}]({url})")
                else:
                    st.markdown(f"{file_instance['video_name']}")


def mqtt_set_status(mqtt_status, text):
    state.mqtt_status = text
    mqtt_status.empty()
    mqtt_status.markdown(f"**MQTT status:** {text}")


def _on_connect(client, userdata, flags, rc):
    if rc == 0:
        state.mqtt_connected = True


def _on_message(client, userdata, msg):
    # This is the only topic the frontend subscribes to
    assert msg.topic == MQTT_TOPIC_STATUS
    if not state.selected_device:
        return

    message = json.loads(msg.payload.decode())
    if message["device_id"] != state.selected_device:
        return

    state.mqtt_last_status = message


@st.cache(allow_output_mutation=True)
def restore_client():
    client = mqtt_client.Client(MQTT_CLIENT_ID)
    client.connect(MQTT_BROKER, MQTT_BROKER_PORT)
    return client


def get_mqtt_client():
    client = restore_client()
    client.on_connect = _on_connect
    client.on_message = _on_message
    return client


def mqtt_wait_connection(client, timeout):
    while not state.mqtt_connected and timeout:
        client.loop(timeout=1)
        timeout -= 1


def mqtt_wait_response(client, timeout):
    while not state.mqtt_last_status and timeout:
        client.loop(timeout=1)
        timeout -= 1


def send_mqtt_message_wait_response(topic, message, mqtt_status):
    # This function connects, sends a message and waits for the reply. Reconnects as needed
    try:
        client = get_mqtt_client()
        if not state.mqtt_connected:
            mqtt_set_status(mqtt_status, "正在連接...")
            mqtt_wait_connection(client, 5)

        state.mqtt_last_status = None  # Reset status to await updated response

        # Since we're not running the client.loop() permanently,
        # the way to ensure that the MQTT client is connected is to try
        # to send a message and if it fails, try reconnecting.
        retry_publish = 2  # mosquitto disconnects after a while so at least use 2 here
        while retry_publish:
            retry_publish -= 1
            client.subscribe(MQTT_TOPIC_STATUS)  # Must be done after reconnection
            msg_info = client.publish(topic, json.dumps(message))
            mqtt_set_status(mqtt_status, "Sending message...")

            timeout = 5
            while not msg_info.rc and not msg_info.is_published() and timeout:
                client.loop(1)
                timeout -= 1

            if msg_info.is_published():
                mqtt_set_status(mqtt_status, ":clock3: 等待設備響應...")
                retry_publish = 0  # Success: exit retry loop
            elif msg_info.rc:
                state.mqtt_connected = False
                mqtt_set_status(mqtt_status, ":clock3: 正在重新連接...")
                client.reconnect()
                mqtt_wait_connection(client, 5)

        if not msg_info.is_published():
            mqtt_set_status(mqtt_status, ":o: Message failed")
            return

        mqtt_wait_response(client, 5)
        if not state.mqtt_last_status:
            mqtt_set_status(mqtt_status, ":red_circle: 設備無響應")

    except Exception as e:
        mqtt_set_status(mqtt_status, f":red_circle: 無法連接到 MQTT 代理: {e}")


def send_mqtt_command(device_id, command, mqtt_status):
    send_mqtt_message_wait_response(
        MQTT_TOPIC_COMMANDS, {"device_id": device_id, "command": command}, mqtt_status
    )


def main():
    st.set_page_config(page_title="Maskcam")

    st.title("口罩辨識儀表板")
    all_devices = get_devices()
    display_sidebar(all_devices, state)

    if state.selected_device is None:
        st.write("Please select a device.")
    else:
        display_device(state)

    state.sync()


if __name__ == "__main__":
    main()
