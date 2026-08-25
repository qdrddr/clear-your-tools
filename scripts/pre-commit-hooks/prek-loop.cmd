@echo off
setlocal EnableExtensions

set "BASH="
for %%I in ("C:\Program Files\Git\bin\bash.exe" "C:\Program Files (x86)\Git\bin\bash.exe" "%ProgramFiles%\Git\bin\bash.exe") do (
	if not defined BASH if exist %%~I set "BASH=%%~I"
)
if not defined BASH (
	for /f "delims=" %%I in ('where bash 2^>nul') do (
		if not defined BASH set "BASH=%%I"
	)
)
if not defined BASH (
	echo error: bash not found; install Git for Windows or add Git\bin to PATH >&2
	exit /b 127
)

"%BASH%" "%~dp0prek-loop.sh" %*
exit /b %ERRORLEVEL%
