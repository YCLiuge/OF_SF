$ErrorActionPreference = "Stop"
Add-Type -AssemblyName PresentationCore
$source = [Console]::OpenStandardInput()
$buffer = New-Object IO.MemoryStream
$source.CopyTo($buffer)
$buffer.Position = 0
$bitmap = New-Object Windows.Media.Imaging.BitmapImage
$bitmap.BeginInit()
$bitmap.CacheOption = [Windows.Media.Imaging.BitmapCacheOption]::OnLoad
$bitmap.DecodePixelWidth = @WIDTH@
$bitmap.DecodePixelHeight = @HEIGHT@
$bitmap.StreamSource = $buffer
$bitmap.EndInit()
$rgb = New-Object Windows.Media.Imaging.FormatConvertedBitmap
$rgb.BeginInit()
$rgb.Source = $bitmap
$rgb.DestinationFormat = [Windows.Media.PixelFormats]::Rgb24
$rgb.EndInit()
$encoder = New-Object Windows.Media.Imaging.PngBitmapEncoder
$encoder.Frames.Add([Windows.Media.Imaging.BitmapFrame]::Create($rgb))
# WPF's encoder seeks during PNG creation; standard output can be a pipe.
$output = New-Object IO.MemoryStream
$encoder.Save($output)
$output.Position = 0
$output.CopyTo([Console]::OpenStandardOutput())
