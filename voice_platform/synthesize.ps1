$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$synthesizer = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    if ($env:VOICE_TTS_VOICE) { $synthesizer.SelectVoice($env:VOICE_TTS_VOICE) }
    $format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
    $synthesizer.SetOutputToWaveFile($env:VOICE_TTS_OUTPUT, $format)
    $synthesizer.Speak([System.IO.File]::ReadAllText($env:VOICE_TTS_INPUT, [System.Text.Encoding]::UTF8))
} finally {
    $synthesizer.Dispose()
}
