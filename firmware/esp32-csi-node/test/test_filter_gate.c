/* SYNTHETIC: a foreign frame must not consume the rate-gate slot of a
 * MAC-filtered (dedicated transmitter) link. */
#include <assert.h>
#include <stdint.h>

static int64_t s_now_us = 1000000;
static int64_t test_time(void) { return s_now_us; }
#define esp_timer_get_time test_time
#include "../main/csi_collector.c"

nvs_config_t g_nvs_config;

int stream_sender_send_priority(const uint8_t *data, size_t len)
{
    (void)data;
    return (int)len;
}

bool edge_enqueue_csi(const uint8_t *iq_data, uint16_t iq_len,
                      int8_t rssi, uint8_t channel)
{
    (void)iq_data; (void)iq_len; (void)rssi; (void)channel;
    return true;
}

uint64_t c6_sync_espnow_get_epoch_us(void) { return 0; }
int64_t c6_sync_espnow_get_offset_us_smoothed(void) { return 0; }
bool c6_sync_espnow_is_leader(void) { return false; }

static void make_info(wifi_csi_info_t *info, int8_t *iq, uint8_t last_mac_byte)
{
    memset(info, 0, sizeof(*info));
    info->rx_ctrl.channel = 6;
    info->rx_ctrl.rssi = -50;
    info->len = 8;
    info->buf = iq;
    memset(info->mac, 0x11, sizeof(info->mac));
    info->mac[5] = last_mac_byte;
}

int main(void)
{
    int8_t iq[8] = {1, 2, 3, 4, 5, 6, 7, 8};
    wifi_csi_info_t foreign, sender;
    make_info(&foreign, iq, 0xAA);
    make_info(&sender, iq, 0xBB);

    memcpy(s_filter_mac, sender.mac, sizeof(s_filter_mac));
    s_filter_mac_set = true;

    /* Foreign AP frame first, sender frame later in the same 20 ms window. */
    wifi_csi_callback(NULL, &foreign);
    assert(s_cb_count == 0 && s_early_drop == 0);
    s_now_us += 1000;
    wifi_csi_callback(NULL, &sender);
    assert(s_cb_count == 1);

    /* The gate still bounds accepted sender frames to one per window. */
    s_now_us += 1000;
    wifi_csi_callback(NULL, &sender);
    assert(s_cb_count == 1 && s_early_drop == 1);
    s_now_us += CSI_MIN_PROCESS_INTERVAL_US;
    wifi_csi_callback(NULL, &sender);
    assert(s_cb_count == 2);

    puts("PASS: MAC filter runs before the CSI rate gate");
    return 0;
}
