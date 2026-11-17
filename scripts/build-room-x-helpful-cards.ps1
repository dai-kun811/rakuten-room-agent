param(
    [string]$CatalogPath = "config\room-x-helpful-cards.json",
    [string]$BackgroundRoot = "assets\x-helpful\backgrounds"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$catalogFile = Join-Path $projectRoot $CatalogPath
$catalog = Get-Content -LiteralPath $catalogFile -Raw -Encoding UTF8 | ConvertFrom-Json
if ($catalog.version -ne 1 -or $catalog.cards.Count -lt 1) {
    throw "Helpful card catalog is invalid."
}

Add-Type -AssemblyName System.Drawing

function Draw-CardText {
    param(
        [System.Drawing.Graphics]$Graphics,
        [string]$Text,
        [System.Drawing.Font]$Font,
        [System.Drawing.Brush]$Brush,
        [float]$X,
        [float]$Y,
        [float]$Width,
        [float]$Height
    )
    $format = New-Object System.Drawing.StringFormat
    # The catalog text is reviewed content. Never silently replace part of it
    # with an ellipsis; the fixed templates below are sized to fit it exactly.
    $format.Trimming = [System.Drawing.StringTrimming]::None
    $rectangle = New-Object System.Drawing.RectangleF($X, $Y, $Width, $Height)
    $Graphics.DrawString($Text, $Font, $Brush, $rectangle, $format)
    $format.Dispose()
}

function New-RoundedPath {
    param([float]$X, [float]$Y, [float]$Width, [float]$Height, [float]$Radius)
    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $diameter = $Radius * 2
    $path.AddArc($X, $Y, $diameter, $diameter, 180, 90)
    $path.AddArc($X + $Width - $diameter, $Y, $diameter, $diameter, 270, 90)
    $path.AddArc($X + $Width - $diameter, $Y + $Height - $diameter, $diameter, $diameter, 0, 90)
    $path.AddArc($X, $Y + $Height - $diameter, $diameter, $diameter, 90, 90)
    $path.CloseFigure()
    return $path
}

function Draw-CoverImage {
    param(
        [System.Drawing.Graphics]$Graphics,
        [System.Drawing.Image]$Image,
        [int]$Width,
        [int]$Height
    )
    $scale = [Math]::Max($Width / $Image.Width, $Height / $Image.Height)
    $sourceWidth = $Width / $scale
    $sourceHeight = $Height / $scale
    $sourceX = ($Image.Width - $sourceWidth) / 2
    $sourceY = ($Image.Height - $sourceHeight) / 2
    $destination = New-Object System.Drawing.RectangleF 0, 0, $Width, $Height
    $source = New-Object System.Drawing.RectangleF $sourceX, $sourceY, $sourceWidth, $sourceHeight
    $Graphics.DrawImage($Image, $destination, $source, [System.Drawing.GraphicsUnit]::Pixel)
}

$accent = [System.Drawing.Color]::FromArgb(43, 111, 171)
$ink = [System.Drawing.Color]::FromArgb(28, 42, 54)
$muted = [System.Drawing.Color]::FromArgb(70, 82, 91)
$cream = [System.Drawing.Color]::FromArgb(249, 246, 239)
$patternLabels = @{
    micro_hack_v1 = "とらパパ｜朝の工夫"
    saveable_checklist_v1 = "とらパパ｜保存用チェック"
    before_after_setup_v1 = "とらパパ｜配置の工夫"
    conversation_prompt_v1 = "とらパパ｜家族のヒント"
}

# Keep the useful scene visible. These source images place the parent/child on
# the left, so their copy panel belongs on the otherwise quiet right side.
$rightPanelCards = @(
    "check-03-toy-choice",
    "talk-01-lost-item",
    "talk-03-spare-stock",
    "talk-04-weekend-play"
)

$generated = 0
foreach ($card in $catalog.cards) {
    $backgroundPath = Join-Path (Join-Path $projectRoot $BackgroundRoot) ($card.card_id + ".png")
    if (-not (Test-Path -LiteralPath $backgroundPath -PathType Leaf)) {
        throw "Helpful card background is missing: $($card.card_id)"
    }
    $outputPath = Join-Path $projectRoot $card.visual_path
    $outputDirectory = Split-Path -Parent $outputPath
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

    $bitmap = New-Object System.Drawing.Bitmap 1200, 675
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
    $background = [System.Drawing.Image]::FromFile($backgroundPath)

    $accentBrush = New-Object System.Drawing.SolidBrush($accent)
    $inkBrush = New-Object System.Drawing.SolidBrush($ink)
    $mutedBrush = New-Object System.Drawing.SolidBrush($muted)
    $whiteBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::White)
    $panelBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(238, $cream))
    $shadowBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(45, 12, 24, 35))
    $titleFont = New-Object System.Drawing.Font("Yu Gothic", 28, [System.Drawing.FontStyle]::Bold)
    $pointFont = New-Object System.Drawing.Font("Yu Gothic", 16.5, [System.Drawing.FontStyle]::Bold)
    $smallFont = New-Object System.Drawing.Font("Yu Gothic", 17, [System.Drawing.FontStyle]::Regular)
    $labelFont = New-Object System.Drawing.Font("Yu Gothic", 16, [System.Drawing.FontStyle]::Bold)

    try {
        Draw-CoverImage -Graphics $graphics -Image $background -Width 1200 -Height 675

        if ($card.pattern_id -eq "before_after_setup_v1") {
            $graphics.FillRectangle($shadowBrush, 0, 0, 1200, 194)
            $graphics.FillRectangle($panelBrush, 0, 0, 1200, 180)
            $labelPath = New-RoundedPath -X 54 -Y 28 -Width 330 -Height 38 -Radius 19
            $graphics.FillPath($accentBrush, $labelPath)
            Draw-CardText -Graphics $graphics -Text $patternLabels[$card.pattern_id] -Font $labelFont -Brush $whiteBrush -X 72 -Y 31 -Width 294 -Height 34
            Draw-CardText -Graphics $graphics -Text $card.hook -Font $titleFont -Brush $inkBrush -X 54 -Y 78 -Width 1090 -Height 90
            $labelPath.Dispose()
        }
        else {
            $rightPanel = $rightPanelCards -contains $card.card_id
            $panelX = if ($rightPanel) { 594 } else { 0 }
            $shadowX = if ($rightPanel) { 576 } else { 0 }
            $contentX = if ($rightPanel) { 646 } else { 54 }
            $graphics.FillRectangle($shadowBrush, $shadowX, 0, 624, 675)
            $panelPath = New-RoundedPath -X $panelX -Y 0 -Width 606 -Height 675 -Radius 0.1
            $graphics.FillPath($panelBrush, $panelPath)
            $labelPath = New-RoundedPath -X $contentX -Y 46 -Width 330 -Height 42 -Radius 21
            $graphics.FillPath($accentBrush, $labelPath)
            Draw-CardText -Graphics $graphics -Text $patternLabels[$card.pattern_id] -Font $labelFont -Brush $whiteBrush -X ($contentX + 19) -Y 50 -Width 294 -Height 36
            Draw-CardText -Graphics $graphics -Text $card.hook -Font $titleFont -Brush $inkBrush -X $contentX -Y 120 -Width 500 -Height 185

            $pointY = 320
            foreach ($point in $card.points | Select-Object -First 3) {
                $graphics.FillEllipse($accentBrush, ($contentX + 2), $pointY + 7, 18, 18)
                Draw-CardText -Graphics $graphics -Text ([string]$point) -Font $pointFont -Brush $inkBrush -X ($contentX + 37) -Y $pointY -Width 455 -Height 72
                $pointY += 84
            }
            $panelPath.Dispose()
            $labelPath.Dispose()
        }

        # Force all queued GDI+ text operations to complete before encoding.
        $graphics.Flush([System.Drawing.Drawing2D.FlushIntention]::Sync)
        $bitmap.Save($outputPath, [System.Drawing.Imaging.ImageFormat]::Png)
        $generated += 1
    }
    finally {
        $labelFont.Dispose()
        $smallFont.Dispose()
        $pointFont.Dispose()
        $titleFont.Dispose()
        $shadowBrush.Dispose()
        $panelBrush.Dispose()
        $whiteBrush.Dispose()
        $mutedBrush.Dispose()
        $inkBrush.Dispose()
        $accentBrush.Dispose()
        $background.Dispose()
        $graphics.Dispose()
        $bitmap.Dispose()
    }
}

[pscustomobject]@{
    Generated = $generated
    BackgroundRoot = (Join-Path $projectRoot $BackgroundRoot)
    OutputRoot = (Join-Path $projectRoot "assets\x-helpful")
}
