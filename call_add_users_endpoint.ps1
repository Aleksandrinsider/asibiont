$url = "https://asibiont.ru/admin/add_test_users?secret=aj00yr34Pmg9YM8gWSggYoCMSG8t1a6ahntl4OJyPcw"

try {
    Write-Host "Вызываем endpoint для добавления пользователей в Railway БД..." -ForegroundColor Yellow
    $response = Invoke-RestMethod -Uri $url -Method Get -ErrorAction Stop
    
    Write-Host "`nРезультат:" -ForegroundColor Green
    Write-Host "Success: $($response.success)" -ForegroundColor Cyan
    Write-Host "Total users in DB: $($response.total_users)" -ForegroundColor Cyan
    
    if ($response.added -and $response.added.Count -gt 0) {
        Write-Host "`nДобавлено ($($response.added.Count)):" -ForegroundColor Green
        $response.added | ForEach-Object { Write-Host "  $_" -ForegroundColor Green }
    }
    
    if ($response.skipped -and $response.skipped.Count -gt 0) {
        Write-Host "`nПропущено ($($response.skipped.Count)):" -ForegroundColor Yellow
        $response.skipped | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
    }
    
} catch {
    Write-Host "`nОшибка:" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    if ($_.ErrorDetails.Message) {
        Write-Host $_.ErrorDetails.Message -ForegroundColor Red
    }
}
