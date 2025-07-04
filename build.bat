@echo off
:: 设置命令行编码为 UTF-8，避免出现乱码
chcp 65001

SET VENV_DIR=venv
SET REQUIREMENTS_FILE=requirements.txt

:: Step 1: 检查虚拟环境是否存在
IF NOT EXIST "%VENV_DIR%\Scripts\activate.bat" (
    echo 虚拟环境不存在，正在创建...
    python -m venv %VENV_DIR%
    IF %ERRORLEVEL% NEQ 0 (
        echo 创建虚拟环境失败！请检查是否安装了 Python。
        exit /b 1
    )
)

:: Step 2: 激活虚拟环境
echo 激活虚拟环境...
call %VENV_DIR%\Scripts\activate.bat

:: Step 3: 执行编译
pyinstaller sv-auto.spec

:: Step 4: 准备发布文件夹
IF EXIST release (rd /s /q release)
md release

:: Step 5: 复制可执行文件
copy dist\sv-auto.exe release\

:: Step 6: 复制资源文件夹
xcopy /E /I "国服覆盖资源" release\国服覆盖资源
xcopy /E /I "国际服覆盖资源" release\国际服覆盖资源
xcopy /E /I shield release\shield

:: Step 7: 复制国服覆盖资源内容到根目录
xcopy /E /I "国服覆盖资源\*" release\

:: Step 8: 复制配置文件模板并命名为config.json
copy config-template.json release\config.json