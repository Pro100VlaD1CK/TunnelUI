@echo off
setlocal

if exist ".agents\skills\impeccable\scripts\impeccable.cmd" goto run
echo impeccable hook launcher is missing 1>&2
exit /b 127

:run
call ".agents\skills\impeccable\scripts\impeccable.cmd" hook
set "impeccable_exit=%errorlevel%"
exit /b %impeccable_exit%
