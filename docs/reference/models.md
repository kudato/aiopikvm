# Models

All response models inherit from a base class with `extra="allow"` for forward-compatible parsing.

## ATX

::: aiopikvm.ATXState
    options:
      show_bases: false

::: aiopikvm.ATXActs
    options:
      show_bases: false

::: aiopikvm.ATXLeds
    options:
      show_bases: false

## HID

::: aiopikvm.HIDState
    options:
      show_bases: false

::: aiopikvm.HIDKeyboard
    options:
      show_bases: false

::: aiopikvm.HIDKeyboardLeds
    options:
      show_bases: false

::: aiopikvm.HIDMouse
    options:
      show_bases: false

::: aiopikvm.HIDOutputs
    options:
      show_bases: false

::: aiopikvm.HIDJiggler
    options:
      show_bases: false

::: aiopikvm.HIDKeymaps
    options:
      show_bases: false

## MSD

::: aiopikvm.MSDState
    options:
      show_bases: false

::: aiopikvm.MSDDrive
    options:
      show_bases: false

::: aiopikvm.MSDStorage
    options:
      show_bases: false

::: aiopikvm.MSDImage
    options:
      show_bases: false

::: aiopikvm.MSDDriveImage
    options:
      show_bases: false

::: aiopikvm.MSDPart
    options:
      show_bases: false

::: aiopikvm.MSDUpload
    options:
      show_bases: false

::: aiopikvm.MSDDownload
    options:
      show_bases: false

## GPIO

::: aiopikvm.GPIOState
    options:
      show_bases: false

::: aiopikvm.GPIOIOState
    options:
      show_bases: false

::: aiopikvm.GPIOChannel
    options:
      show_bases: false

::: aiopikvm.GPIOInput
    options:
      show_bases: false

::: aiopikvm.GPIOModel
    options:
      show_bases: false

::: aiopikvm.GPIOScheme
    options:
      show_bases: false

::: aiopikvm.GPIOOutputScheme
    options:
      show_bases: false

::: aiopikvm.GPIOInputScheme
    options:
      show_bases: false

::: aiopikvm.GPIOPulse
    options:
      show_bases: false

::: aiopikvm.GPIOHardware
    options:
      show_bases: false

::: aiopikvm.GPIOView
    options:
      show_bases: false

::: aiopikvm.GPIOViewHeader
    options:
      show_bases: false

## Streamer

::: aiopikvm.StreamerState
    options:
      show_bases: false

::: aiopikvm.Streamer
    options:
      show_bases: false

::: aiopikvm.StreamerSource
    options:
      show_bases: false

::: aiopikvm.Resolution
    options:
      show_bases: false

::: aiopikvm.StreamerParams
    options:
      show_bases: false

::: aiopikvm.StreamerLimits
    options:
      show_bases: false

::: aiopikvm.StreamerLimitRange
    options:
      show_bases: false

::: aiopikvm.StreamerFeatures
    options:
      show_bases: false

::: aiopikvm.SnapshotImage
    options:
      show_bases: false

::: aiopikvm.StreamerSnapshot
    options:
      show_bases: false

::: aiopikvm.SavedSnapshot
    options:
      show_bases: false

::: aiopikvm.StreamerEncoder
    options:
      show_bases: false

::: aiopikvm.StreamerH264
    options:
      show_bases: false

::: aiopikvm.StreamerSinks
    options:
      show_bases: false

::: aiopikvm.StreamerSinkInfo
    options:
      show_bases: false

::: aiopikvm.StreamerStream
    options:
      show_bases: false

::: aiopikvm.StreamerClientStat
    options:
      show_bases: false

::: aiopikvm.MJPEGFrame
    options:
      show_bases: false

::: aiopikvm.OCRInfo
    options:
      show_bases: false

::: aiopikvm.OCRLangs
    options:
      show_bases: false

## Media

::: aiopikvm.MediaState
    options:
      show_bases: false

::: aiopikvm.MediaVideoFormats
    options:
      show_bases: false

::: aiopikvm.MediaH264
    options:
      show_bases: false

::: aiopikvm.MediaJPEG
    options:
      show_bases: false

::: aiopikvm.MediaFrame
    options:
      show_bases: false

## Switch

::: aiopikvm.SwitchState
    options:
      show_bases: false

::: aiopikvm.SwitchSummary
    options:
      show_bases: false

::: aiopikvm.SwitchModel
    options:
      show_bases: false

::: aiopikvm.SwitchPort
    options:
      show_bases: false

::: aiopikvm.SwitchPortAtx
    options:
      show_bases: false

::: aiopikvm.SwitchPortVideo
    options:
      show_bases: false

::: aiopikvm.SwitchAtxClickDelays
    options:
      show_bases: false

::: aiopikvm.SwitchUnit
    options:
      show_bases: false

::: aiopikvm.SwitchUnitFirmware
    options:
      show_bases: false

::: aiopikvm.SwitchFirmware
    options:
      show_bases: false

::: aiopikvm.SwitchLimits
    options:
      show_bases: false

::: aiopikvm.SwitchAtxLimits
    options:
      show_bases: false

::: aiopikvm.SwitchAtxClickDelayLimits
    options:
      show_bases: false

::: aiopikvm.SwitchAtxClickDelayLimit
    options:
      show_bases: false

::: aiopikvm.SwitchEdids
    options:
      show_bases: false

::: aiopikvm.EDID
    options:
      show_bases: false

::: aiopikvm.EDIDInfo
    options:
      show_bases: false

::: aiopikvm.SwitchColors
    options:
      show_bases: false

::: aiopikvm.SwitchColor
    options:
      show_bases: false

::: aiopikvm.SwitchLinks
    options:
      show_bases: false

::: aiopikvm.SwitchBeacons
    options:
      show_bases: false

::: aiopikvm.SwitchAtx
    options:
      show_bases: false

::: aiopikvm.SwitchAtxLeds
    options:
      show_bases: false

## Info

::: aiopikvm.InfoState
    options:
      show_bases: false

::: aiopikvm.InfoAuth
    options:
      show_bases: false

::: aiopikvm.InfoNode
    options:
      show_bases: false

::: aiopikvm.InfoUptime
    options:
      show_bases: false

::: aiopikvm.InfoUptimeParts
    options:
      show_bases: false

::: aiopikvm.InfoHealth
    options:
      show_bases: false

::: aiopikvm.InfoTemp
    options:
      show_bases: false

::: aiopikvm.InfoCPU
    options:
      show_bases: false

::: aiopikvm.InfoMem
    options:
      show_bases: false

::: aiopikvm.InfoThrottling
    options:
      show_bases: false

::: aiopikvm.InfoThrottlingFlags
    options:
      show_bases: false

::: aiopikvm.InfoThrottlingFlag
    options:
      show_bases: false

::: aiopikvm.InfoFan
    options:
      show_bases: false

::: aiopikvm.InfoSystem
    options:
      show_bases: false

::: aiopikvm.InfoKvmd
    options:
      show_bases: false

::: aiopikvm.InfoKernel
    options:
      show_bases: false

::: aiopikvm.InfoStreamer
    options:
      show_bases: false

::: aiopikvm.InfoPlatform
    options:
      show_bases: false

::: aiopikvm.InfoExtra
    options:
      show_bases: false
