#include "UnrealMCP.h"

#include "HAL/FileManager.h"
#include "IPythonScriptPlugin.h"
#include "Misc/Paths.h"
#include "Modules/ModuleManager.h"

IMPLEMENT_MODULE(FUnrealMCPModule, UnrealMCP)

void FUnrealMCPModule::StartupModule()
{
    UE_LOG(LogTemp, Log, TEXT("UnrealMCP: StartupModule"));

    if (GEngine)
    {
        GEngine->OnPostEditorTick().AddLambda([this](float /*DeltaTime*/)
        {
            static bool bStarted = false;
            if (!bStarted)
            {
                bStarted = true;
                BootstrapPythonBridge();
            }
        });
    }
    else
    {
        BootstrapPythonBridge();
    }
}

void FUnrealMCPModule::ShutdownModule()
{
    UE_LOG(LogTemp, Log, TEXT("UnrealMCP: ShutdownModule - stopping HTTP bridge"));

    IPythonScriptPlugin* PythonPlugin = FModuleManager::GetModulePtr<IPythonScriptPlugin>("PythonScriptPlugin");
    if (PythonPlugin && PythonPlugin->IsPythonAvailable())
    {
        PythonPlugin->ExecPythonCommand(TEXT("import plugin_server; plugin_server.stop()"));
    }
}

FString FUnrealMCPModule::GetServerScriptPath() const
{
    FString PluginDir = FPaths::Combine(FPaths::ProjectPluginsDir(), TEXT("UnrealMCP"));
    if (!IFileManager::Get().DirectoryExists(*PluginDir))
    {
        PluginDir = FPaths::Combine(FPaths::EnginePluginsDir(), TEXT("UnrealMCP"));
    }

    return FPaths::Combine(PluginDir, TEXT("Content"), TEXT("plugin_server.py"));
}

void FUnrealMCPModule::BootstrapPythonBridge()
{
    IPythonScriptPlugin* PythonPlugin = FModuleManager::GetModulePtr<IPythonScriptPlugin>("PythonScriptPlugin");

    if (!PythonPlugin || !PythonPlugin->IsPythonAvailable())
    {
        UE_LOG(LogTemp, Error, TEXT("UnrealMCP: Python not available; enable PythonScriptPlugin."));
        return;
    }

    const FString ScriptPath = GetServerScriptPath();
    if (!IFileManager::Get().FileExists(*ScriptPath))
    {
        UE_LOG(LogTemp, Error, TEXT("UnrealMCP: plugin_server.py missing at %s"), *ScriptPath);
        return;
    }

    const FString Cmd = FString::Printf(TEXT("exec(open(r'%s').read())"), *ScriptPath);
    PythonPlugin->ExecPythonCommand(*Cmd);

    UE_LOG(LogTemp, Log, TEXT("UnrealMCP: Python HTTP bridge started on 127.0.0.1:8765"));
}
