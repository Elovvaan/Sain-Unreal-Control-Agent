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
        TickDelegateHandle = GEngine->OnPostEditorTick().AddLambda([this](float /*DeltaTime*/)
        {
            if (!bBridgeStarted)
            {
                bBridgeStarted = true;
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

    // Remove the tick delegate if bootstrap never fired (e.g. very early shutdown).
    if (GEngine && TickDelegateHandle.IsValid())
    {
        GEngine->OnPostEditorTick().Remove(TickDelegateHandle);
        TickDelegateHandle.Reset();
    }

    IPythonScriptPlugin* PythonPlugin = FModuleManager::GetModulePtr<IPythonScriptPlugin>("PythonScriptPlugin");
    if (PythonPlugin && PythonPlugin->IsPythonAvailable())
    {
        PythonPlugin->ExecPythonCommand(TEXT(
            "import sys\n"
            "_mod = sys.modules.get('unrealmcp_bridge')\n"
            "if _mod:\n"
            "    try:\n"
            "        _mod.stop()\n"
            "    except Exception as _e:\n"
            "        import unreal; unreal.log(f'UnrealMCP: error stopping bridge: {_e}')\n"
            "else:\n"
            "    import unreal; unreal.log('UnrealMCP: bridge module not found in sys.modules; nothing to stop')\n"
        ));
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

    // Use importlib to load the script under a unique module name so ShutdownModule
    // can reliably locate and stop it via sys.modules['unrealmcp_bridge'], regardless
    // of other plugin_server modules that may exist on sys.path.
    const FString Cmd = FString::Printf(
        TEXT("import importlib.util, sys\n"
             "_spec = importlib.util.spec_from_file_location('unrealmcp_bridge', r'%s')\n"
             "_mod = importlib.util.module_from_spec(_spec)\n"
             "sys.modules['unrealmcp_bridge'] = _mod\n"
             "_spec.loader.exec_module(_mod)"),
        *ScriptPath);
    PythonPlugin->ExecPythonCommand(*Cmd);

    UE_LOG(LogTemp, Log, TEXT("UnrealMCP: Python HTTP bridge started on 127.0.0.1:8765"));
}
