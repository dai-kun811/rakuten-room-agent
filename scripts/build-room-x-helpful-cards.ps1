param(
    [string]$CatalogPath = "config\room-x-helpful-cards.json"
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
    $format.Trimming = [System.Drawing.StringTrimming]::EllipsisWord
    $format.FormatFlags = [System.Drawing.StringFormatFlags]::LineLimit
    $rectangle = New-Object System.Drawing.RectangleF($X, $Y, $Width, $Height)
    $Graphics.DrawString($Text, $Font, $Brush, $rectangle, $format)
    $format.Dispose()
}

$palette = @(
    @{ Background = [System.Drawing.Color]::FromArgb(244, 249, 255); Accent = [System.Drawing.Color]::FromArgb(44, 111, 187) },
    @{ Background = [System.Drawing.Color]::FromArgb(255, 249, 239); Accent = [System.Drawing.Color]::FromArgb(215, 112, 52) },
    @{ Background = [System.Drawing.Color]::FromArgb(244, 252, 246); Accent = [System.Drawing.Color]::FromArgb(56, 139, 91) },
    @{ Background = [System.Drawing.Color]::FromArgb(252, 245, 252); Accent = [System.Drawing.Color]::FromArgb(139, 76, 151) }
)

$index = 0
foreach ($card in $catalog.cards) {
    $outputPath = Join-Path $projectRoot $card.visual_path
    $outputDirectory = Split-Path -Parent $outputPath
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
    $colors = $palette[$index % $palette.Count]
    $bitmap = New-Object System.Drawing.Bitmap 1200, 675
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
    $graphics.Clear($colors.Background)

    $accentBrush = New-Object System.Drawing.SolidBrush($colors.Accent)
    $textBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(35, 42, 52))
    $mutedBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(84, 94, 107))
    $whiteBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::White)
    $titleFont = New-Object System.Drawing.Font("Yu Gothic", 38, [System.Drawing.FontStyle]::Bold)
    $pointFont = New-Object System.Drawing.Font("Yu Gothic", 27, [System.Drawing.FontStyle]::Regular)
    $smallFont = New-Object System.Drawing.Font("Yu Gothic", 18, [System.Drawing.FontStyle]::Regular)
    $labelFont = New-Object System.Drawing.Font("Yu Gothic", 17, [System.Drawing.FontStyle]::Bold)

    $graphics.FillRectangle($accentBrush, 0, 0, 1200, 18)
    $graphics.FillRectangle($accentBrush, 72, 54, 225, 44)
    $graphics.DrawString("とらパパの育児メモ", $labelFont, $whiteBrush, 87, 62)
    Draw-CardText -Graphics $graphics -Text $card.hook -Font $titleFont -Brush $textBrush -X 72 -Y 125 -Width 1055 -Height 125

    $pointY = 272
    $pointNumber = 1
    foreach ($point in $card.points) {
        $graphics.FillEllipse($accentBrush, 78, $pointY + 4, 42, 42)
        $graphics.DrawString([string]$pointNumber, $labelFont, $whiteBrush, 91, $pointY + 10)
        Draw-CardText -Graphics $graphics -Text ([string]$point) -Font $pointFont -Brush $textBrush -X 142 -Y $pointY -Width 970 -Height 64
        $pointY += 76
        $pointNumber += 1
    }

    Draw-CardText -Graphics $graphics -Text $card.caution -Font $smallFont -Brush $mutedBrush -X 76 -Y 535 -Width 1045 -Height 62
    $graphics.FillRectangle($accentBrush, 0, 625, 1200, 50)
    $graphics.DrawString("保存して、必要な日に見返せる1枚に。", $smallFont, $whiteBrush, 72, 636)

    $bitmap.Save($outputPath, [System.Drawing.Imaging.ImageFormat]::Png)
    $labelFont.Dispose()
    $smallFont.Dispose()
    $pointFont.Dispose()
    $titleFont.Dispose()
    $whiteBrush.Dispose()
    $mutedBrush.Dispose()
    $textBrush.Dispose()
    $accentBrush.Dispose()
    $graphics.Dispose()
    $bitmap.Dispose()
    $index += 1
}

[pscustomobject]@{
    Generated = $catalog.cards.Count
    OutputRoot = (Join-Path $projectRoot "assets\x-helpful")
}
