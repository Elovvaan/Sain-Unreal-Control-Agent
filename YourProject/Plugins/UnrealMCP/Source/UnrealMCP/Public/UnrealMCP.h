#pragma once

#include "Modules/ModuleManager.h"

class FUnrealMCPModule : public IModuleInterface
{
public:
    virtual void StartupModule() override;
    virtual void ShutdownModule() override;

private:
    FString GetServerScriptPath() const;
    void BootstrapPythonBridge();

    /** Handle for the post-editor-tick delegate; removed in ShutdownModule. */
    FDelegateHandle TickDelegateHandle;

    /** Guards against re-entrant or repeated bootstrap calls. */
    bool bBridgeStarted = false;
};
