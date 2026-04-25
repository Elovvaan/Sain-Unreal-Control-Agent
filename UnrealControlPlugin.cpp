#include "UnrealControlPlugin.h"
#include "Modules/ModuleManager.h"
#include "Misc/Paths.h"
#include "HAL/FileManager.h"

// Python scripting plugin interface
#include "IPythonScriptPlugin.h"

IMPLEMENT_MODULE(FUnrealControlPluginModule, UnrealControlPlugin)

// ---------------------------------------------------------------------------
// Startup / Shutdown
// ---------------------------------------------------------------------------

void FUnrealControlPluginModule::StartupModule()
{
    UE_LOG(LogTemp, Log, TEXT("UnrealControlPlugin: StartupModule"));

    // Defer bootstrap until Python is fully initialized.
    // PostEngineInit loading phase guarantees PythonScriptPlugin is ready,
    // but we add an extra tick-deferred call for safety.
    if (GEngine)
    {
        GEngine->OnPostEditorTick().AddLambda([this](float /*DeltaTime*/)
        {
            // Only run once
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
        // Headless / commandlet fallback
        BootstrapPythonBridge();
    }
}

void FUnrealControlPluginModule::ShutdownModule()
{
    UE_LOG(LogTemp, Log, TEXT("UnrealControlPlugin: ShutdownModule — stopping HTTP bridge"));

    IPythonScriptPlugin* PythonPlugin = FModuleManager::GetModulePtr<IPythonScriptPlugin>("PythonScriptPlugin");
    if (PythonPlugin && PythonPlugin->IsPythonAvailable())
    {
        PythonPlugin->ExecPythonCommand(TEXT("import plugin_server; plugin_server.stop()"));
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

FString FUnrealControlPluginModule::GetServerScriptPath() const
{
    // Resolve: <PluginDir>/Content/plugin_server.py
    FString PluginDir = FPaths::Combine(
        FPaths::ProjectPluginsDir(), TEXT("UnrealControlPlugin"));

    // Try project plugins first, then engine plugins
    if (!IFileManager::Get().DirectoryExists(*PluginDir))
    {
        PluginDir = FPaths::Combine(
            FPaths::EnginePluginsDir(), TEXT("UnrealControlPlugin"));
    }

    return FPaths::Combine(PluginDir, TEXT("Content"), TEXT("plugin_server.py"));
}

void FUnrealControlPluginModule::BootstrapPythonBridge()
{
    IPythonScriptPlugin* PythonPlugin =
        FModuleManager::GetModulePtr<IPythonScriptPlugin>("PythonScriptPlugin");

    if (!PythonPlugin || !PythonPlugin->IsPythonAvailable())
    {
        UE_LOG(LogTemp, Error,
               TEXT("UnrealControlPlugin: Python not available — HTTP bridge will NOT start. "
                    "Enable PythonScriptPlugin in your project."));
        return;
    }

    FString ScriptPath = GetServerScriptPath();
    if (!IFileManager::Get().FileExists(*ScriptPath))
    {
        UE_LOG(LogTemp, Error,
               TEXT("UnrealControlPlugin: plugin_server.py not found at: %s"), *ScriptPath);
        return;
    }

    // exec the file — this calls start() at module scope
    FString Cmd = FString::Printf(TEXT("exec(open(r'%s').read())"), *ScriptPath);
    PythonPlugin->ExecPythonCommand(*Cmd);

    UE_LOG(LogTemp, Log,
           TEXT("UnrealControlPlugin: Python HTTP bridge started on 127.0.0.1:8765"));
}
