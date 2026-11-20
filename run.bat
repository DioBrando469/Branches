@echo off
echo ====================================================
echo Starting Dependency Graph Visualizer
echo ====================================================
echo.

REM Run the main script, explicitly pointing to the config in the test folder
python src\main.py --config test\config.toml

echo.
echo ====================================================
echo Execution finished. Check the root folder for generated files.
pause