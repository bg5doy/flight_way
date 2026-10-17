/* 打印 SDRplay API 结构体的 sizeof / offsetof，用于校�?Python ctypes 布局 */
#include <stdio.h>
#include <stddef.h>

#include "sdrplay_api.h"          /* �?DeviceT / DeviceParamsT；GCC 下自�?HANDLE */
#include "sdrplay_api_dev.h"
#include "sdrplay_api_tuner.h"
#include "sdrplay_api_control.h"
#include "sdrplay_api_rx_channel.h"
#include "sdrplay_api_callback.h"

#define SZ(T)      printf("%-24s size=%zu\n", #T, sizeof(T))
#define OFF(T, f)  printf("%-24s offsetof(%s)=%zu\n", #T, #f, offsetof(T, f))

int main(void) {
    printf("== sizes ==\n");
    SZ(sdrplay_api_DeviceT);
    SZ(sdrplay_api_FsFreqT);
    SZ(sdrplay_api_SyncUpdateT);
    SZ(sdrplay_api_ResetFlagsT);
    SZ(sdrplay_api_Rsp1aParamsT);
    SZ(sdrplay_api_Rsp2ParamsT);
    SZ(sdrplay_api_RspDuoParamsT);
    SZ(sdrplay_api_RspDxParamsT);
    SZ(sdrplay_api_DevParamsT);
    SZ(sdrplay_api_GainValuesT);
    SZ(sdrplay_api_GainT);
    SZ(sdrplay_api_RfFreqT);
    SZ(sdrplay_api_DcOffsetTunerT);
    SZ(sdrplay_api_TunerParamsT);
    SZ(sdrplay_api_DcOffsetT);
    SZ(sdrplay_api_DecimationT);
    SZ(sdrplay_api_AgcT);
    SZ(sdrplay_api_ControlParamsT);
    SZ(sdrplay_api_Rsp1aTunerParamsT);
    SZ(sdrplay_api_Rsp2TunerParamsT);
    SZ(sdrplay_api_RspDuo_ResetSlaveFlagsT);
    SZ(sdrplay_api_RspDuoTunerParamsT);
    SZ(sdrplay_api_RspDxTunerParamsT);
    SZ(sdrplay_api_RxChannelParamsT);
    SZ(sdrplay_api_DeviceParamsT);
    SZ(sdrplay_api_StreamCbParamsT);
    SZ(sdrplay_api_GainCbParamT);
    SZ(sdrplay_api_EventParamsT);
    SZ(sdrplay_api_CallbackFnsT);

    printf("\n== key offsets ==\n");
    OFF(sdrplay_api_DeviceT, hwVer);
    OFF(sdrplay_api_DeviceT, tuner);
    OFF(sdrplay_api_DeviceT, rspDuoMode);
    OFF(sdrplay_api_DeviceT, valid);
    OFF(sdrplay_api_DeviceT, rspDuoSampleFreq);
    OFF(sdrplay_api_DeviceT, dev);

    OFF(sdrplay_api_DevParamsT, ppm);
    OFF(sdrplay_api_DevParamsT, fsFreq);
    OFF(sdrplay_api_DevParamsT, syncUpdate);
    OFF(sdrplay_api_DevParamsT, resetFlags);
    OFF(sdrplay_api_DevParamsT, mode);
    OFF(sdrplay_api_DevParamsT, samplesPerPkt);
    OFF(sdrplay_api_DevParamsT, rsp1aParams);
    OFF(sdrplay_api_DevParamsT, rsp2Params);
    OFF(sdrplay_api_DevParamsT, rspDuoParams);
    OFF(sdrplay_api_DevParamsT, rspDxParams);

    OFF(sdrplay_api_TunerParamsT, bwType);
    OFF(sdrplay_api_TunerParamsT, ifType);
    OFF(sdrplay_api_TunerParamsT, loMode);
    OFF(sdrplay_api_TunerParamsT, gain);
    OFF(sdrplay_api_TunerParamsT, rfFreq);
    OFF(sdrplay_api_TunerParamsT, dcOffsetTuner);

    OFF(sdrplay_api_GainT, gRdB);
    OFF(sdrplay_api_GainT, LNAstate);
    OFF(sdrplay_api_GainT, minGr);
    OFF(sdrplay_api_GainT, gainVals);

    OFF(sdrplay_api_AgcT, enable);
    OFF(sdrplay_api_AgcT, setPoint_dBfs);
    OFF(sdrplay_api_AgcT, attack_ms);
    OFF(sdrplay_api_AgcT, decay_threshold_dB);
    OFF(sdrplay_api_AgcT, syncUpdate);

    OFF(sdrplay_api_ControlParamsT, dcOffset);
    OFF(sdrplay_api_ControlParamsT, decimation);
    OFF(sdrplay_api_ControlParamsT, agc);
    OFF(sdrplay_api_ControlParamsT, adsbMode);

    OFF(sdrplay_api_RxChannelParamsT, tunerParams);
    OFF(sdrplay_api_RxChannelParamsT, ctrlParams);
    OFF(sdrplay_api_RxChannelParamsT, rsp1aTunerParams);
    OFF(sdrplay_api_RxChannelParamsT, rsp2TunerParams);
    OFF(sdrplay_api_RxChannelParamsT, rspDuoTunerParams);
    OFF(sdrplay_api_RxChannelParamsT, rspDxTunerParams);

    OFF(sdrplay_api_CallbackFnsT, StreamACbFn);
    OFF(sdrplay_api_CallbackFnsT, StreamBCbFn);
    OFF(sdrplay_api_CallbackFnsT, EventCbFn);
    return 0;
}
